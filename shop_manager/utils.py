from django.db import transaction
from .models import Stock, StockTransaction
from django.core.exceptions import ValidationError


@transaction.atomic
def update_stock(
    product, quantity_change, transaction_type, reason="", reference="", user=None
):
    """
    Atomically update stock and create transaction record.
    Use this in all viewsets to ensure consistency.
    """
    stock = Stock.objects.select_for_update().get(product=product)

    new_quantity = stock.quantity + quantity_change
    if new_quantity < 0:
        raise ValidationError(
            f"Insufficient stock for {product.name}. Available: {stock.quantity}"
        )

    stock.quantity = new_quantity
    stock.save(update_fields=["quantity"])

    StockTransaction.objects.create(
        stock=stock,
        type=transaction_type,
        quantity_change=quantity_change,
        reason=reason,
        reference=reference,
        created_by=user,
    )

    return stock
