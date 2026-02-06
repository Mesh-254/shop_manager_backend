from decimal import Decimal
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from .models import *
from .serializers import *
from rest_framework.parsers import MultiPartParser, FormParser
from django.core.files.storage import default_storage
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from django.db import transaction
from .signals import recalculate_purchase_total




# ==================== SubscriptionPlan ViewSet ====================

class SubscriptionPlanViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing subscription plan instances.
    """

    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer


# ==================== Shop ViewSet ====================

class ShopViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing Shop instances.
    It supports GET, POST, PUT, PATCH, and DELETE operations.
    """
    queryset = Shop.objects.all()
    serializer_class = ShopSerializer
    # Enable file upload handling
    parser_classes = (MultiPartParser, FormParser)

    def perform_create(self, serializer):
        """
        Custom method to handle the creation of a shop.
        Validates the image and assigns the owner as the currently authenticated user.
        """
        logo = self.request.FILES.get('logo', None)

        # Image validation: size and format checks
        if logo:
            self.validate_image(logo)

        # Save the shop instance
        # Set the owner as the current authenticated user
        serializer.save(owner=self.request.user)

    def perform_update(self, serializer):
        """
        Custom method to handle the update of a shop, including image deletion and validation.
        """
        # Handle logo (image) field
        logo = self.request.FILES.get('logo', None)

        # Image validation: size and format checks
        if logo:
            self.validate_image(logo)

        # Handle logo update (deleting the old logo if there's a new one)
        instance = serializer.instance
        if logo:
            old_logo = instance.logo
            # If the old logo exists and is different from the new one, delete it
            if old_logo and old_logo != logo:
                self.delete_old_logo(old_logo)

        # Save the updated shop instance
        serializer.save()

    def validate_image(self, image):
        """
        Validates the uploaded image to check its file size and type.
        """
        max_size = 5 * 1024 * 1024  # 5MB file size limit
        allowed_extensions = ['jpg', 'jpeg', 'png']

        # Check the image file size
        if image.size > max_size:
            raise ValidationError(
                f"File size exceeds the {max_size // (1024 * 1024)}MB limit.")

        # Check the image file extension
        extension = image.name.split('.')[-1].lower()
        if extension not in allowed_extensions:
            raise ValidationError(
                "Invalid file type. Only .jpg, .jpeg, and .png files are allowed.")

    def delete_old_logo(self, old_logo):
        """
        Deletes the old logo file from storage to prevent orphaned files.
        """
        if old_logo:
            try:
                # Check if the file exists and delete it
                if default_storage.exists(old_logo.name):
                    default_storage.delete(old_logo.name)
            except Exception as e:
                raise DjangoValidationError(
                    f"Error deleting old logo: {str(e)}")


# ==================== Category ViewSet ====================

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


# ==================== Supplier ViewSet ====================


class SupplierViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing Supplier instances.
    """

    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer


# ==================== Product ViewSet ====================


class ProductViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing Product instances.
    """

    queryset = Product.objects.all()
    serializer_class = ProductSerializer


# ==================== Category ViewSet ====================


class StockViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing Stock instances.
    """

    queryset = Stock.objects.all()
    serializer_class = StockSerializer


# ==================== Purchase ViewSet ====================


class PurchaseViewSet(viewsets.ModelViewSet):
    """
    A ViewSet to manage Purchases.

    What it does:
    - Allows creating a new Purchase with multiple items
    - Allows updating a Purchase (only limited fields)
    - Disables deleting purchases (optional, see commented code)

    Query optimization:
    - When listing purchases, also pre-load related shop, supplier, creator, and items with their products for speed.
    """

    queryset = Purchase.objects.prefetch_related(
        'items__product'  # Load all related purchase items and their products
    ).select_related(
        'shop',  # Load the shop in a single query
        'supplier',  # Load the supplier in a single query
        'created_by'  # Load the user who created the purchase
    )
    serializer_class = PurchaseSerializer
    # permission_classes = [IsShopOwnerOrReadOnly]  # (Optional) Set who is allowed to access this

    def create(self, request, *args, **kwargs):
        """
        Handle creating a new Purchase record along with its Purchase Items.

        Steps:
        1. Validate the incoming data.
        2. Extract purchase items from the data.
        3. Save the Purchase itself.
        4. Save each Purchase Item.
        5. Update the stock levels for each product.
        6. Recalculate the total cost of the Purchase.

        Notes:
        - This happens inside a database transaction. If any step fails, nothing is saved.
        """

        # Step 1: Validate incoming request data using the serializer
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Step 2: Remove the 'items' from validated data, to handle separately
        items_data = serializer.validated_data.pop('items')

        # Step 3: Start a database transaction
        with transaction.atomic():
            # Create the Purchase record (excluding the purchase items for now)
            purchase = Purchase.objects.create(
                **serializer.validated_data, total_amount=Decimal(0.00))

            # Step 4: Prepare to create multiple PurchaseItem records
            purchase_items = []  # List to hold PurchaseItem objects
            stock_updates = []   # List to hold Stock updates for each product

            # Step 5: Loop through each item to create purchase items and update stock
            for item_data in items_data:
                # Get the actual Product instance
                product = Product.objects.get(pk=item_data['product'].pk)

                # Get the unit cost price for the item (use existing product price if not provided)
                unit_cost_price = Decimal(item_data.get(
                    'unit_cost_price', product.cost_price))

                # Create a PurchaseItem object (but don't save yet)
                purchase_items.append(PurchaseItem(
                    purchase=purchase,
                    product=product,
                    quantity=item_data['quantity'],
                    unit_cost_price=unit_cost_price
                ))

                # Update the Stock quantity for the product
                stock, _ = Stock.objects.get_or_create(product=product)
                stock.quantity += item_data['quantity']
                stock_updates.append(stock)

            # Step 6: Save all Purchase Items at once (bulk create = very fast)
            PurchaseItem.objects.bulk_create(purchase_items)

            # Step 7: Save all updated Stock records at once (bulk update = very fast)
            Stock.objects.bulk_update(stock_updates, ['quantity'])

            # Step 8: Update the total cost of the purchase
            recalculate_purchase_total(purchase)

            # Step 9: Serialize the newly created purchase and return it
            output_serializer = self.get_serializer(purchase)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        """
        Allow updating only specific fields of an existing Purchase.

        Allowed fields for update:
        - payment_status (e.g., Paid, Pending, Overdue)
        - payment_method (e.g., Cash, Card, Bank Transfer)
        - supplier (change supplier if needed)

        If someone tries to update any other field (like purchase items, date, etc), they will get an error.
        """

        # Step 1: Get the existing Purchase instance
        instance = self.get_object()

        # Step 2: Define which fields are allowed to be updated
        allowed_fields = {'payment_status', 'payment_method', 'supplier'}
        incoming_keys = set(request.data.keys())

        # Step 3: Check if the user is trying to update anything else (not allowed)
        if not incoming_keys.issubset(allowed_fields):
            return Response(
                {"detail": "Only payment_status, payment_method, and supplier can be updated."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Step 4: Validate and save the updated data
        serializer = self.get_serializer(
            instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        # Step 5: Return the updated Purchase
        return Response(serializer.data)

    # Optional: You can prevent deleting purchases altogether by uncommenting this:
    # def destroy(self, request, *args, **kwargs):
    #     """
    #     Disable deleting purchases completely.
    #     """
    #     return Response({'detail': 'Deleting purchases is disabled.'}, status=status.HTTP_403_FORBIDDEN)

# ==================== PurchaseItems ViewSet ====================


class PurchaseItemsViewSet(viewsets.ModelViewSet):
    """
    A ViewSet to manage Purchase Items separately.

    What it does:
    - Allows viewing list of all Purchase Items.
    - Allows retrieving a single Purchase Item.
    - Allows editing/updating a Purchase Item.
    - Allows deleting a Purchase Item.

    (Advanced usage: Usually not edited separately unless correcting mistakes)
    """

    queryset = PurchaseItem.objects.all()
    serializer_class = PurchaseItemSerializer


# ==================== SaleViewSet ====================


class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all()
    serializer_class = SaleSerializer

    def create(self, request, *args, **kwargs):
        """
        Create a Sale and its associated SaleItems.
        """
        data = request.data
        serializer = self.get_serializer(data=data)

        # Validate and create the sale and its items
        if serializer.is_valid():
            sale = serializer.save()
            return Response(self.get_serializer(sale).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SaleItemsViewSet(viewsets.ModelViewSet):
    queryset = SaleItem.objects.all()
    serializer_class = SaleItemSerializer

    def create(self, request, *args, **kwargs):
        """
        Create a SaleItem and update the stock accordingly.
        """
        data = request.data
        serializer = self.get_serializer(data=data)

        # Validate and create the sale item
        if serializer.is_valid():
            sale_item = serializer.save()
            return Response(self.get_serializer(sale_item).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)