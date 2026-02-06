from django.db.models.signals import post_save, pre_save, post_delete, pre_delete
from django.dispatch import receiver
from .models import PurchaseItem, Stock, Purchase
from decimal import Decimal


def recalculate_purchase_total(purchase):
    """
    Recalculate and update the total amount of a Purchase
    based on the sum of all its related PurchaseItems.
    """
    total = sum(item.total_cost for item in purchase.items.all())
    purchase.total_amount = total
    purchase.save(update_fields=['total_amount'])


@receiver(pre_save, sender=PurchaseItem)
def adjust_stock_on_update(sender, instance, **kwargs):
    """
    Handle stock adjustments when a PurchaseItem is updated.

    - If the PurchaseItem is new (no primary key), skip (handled in post_save).
    - If an existing PurchaseItem is updated (changing quantity),
      calculate the quantity difference and adjust the stock accordingly.
    """
    if not instance.pk:
        # New instance creation; post_save will handle stock increment
        return

    try:
        # Fetch the existing PurchaseItem from the database
        old_instance = PurchaseItem.objects.get(pk=instance.pk)
    except PurchaseItem.DoesNotExist:
        # If for some reason the old instance is not found, skip
        return

    stock, _ = Stock.objects.get_or_create(product=instance.product)
    quantity_diff = instance.quantity - old_instance.quantity

    if quantity_diff != 0:
        # Adjust stock by the difference
        stock.quantity += quantity_diff
        stock.save(update_fields=['quantity'])


@receiver(post_save, sender=PurchaseItem)
def adjust_stock_on_create(sender, instance, created, **kwargs):
    """
    Handle stock adjustments when a PurchaseItem is created.

    - If newly created, increase the stock quantity by the item quantity.
    - Always recalculate the Purchase total after saving.
    """
    if created:
        stock, _ = Stock.objects.get_or_create(product=instance.product)
        stock.quantity += instance.quantity
        stock.save(update_fields=['quantity'])

    # Update purchase total whenever a PurchaseItem is saved
    recalculate_purchase_total(instance.purchase)


@receiver(post_delete, sender=PurchaseItem)
def adjust_stock_on_delete(sender, instance, **kwargs):
    """
    Handle stock adjustments when a PurchaseItem is deleted.

    - If the PurchaseItem is being deleted as part of deleting a Purchase
      (marked with _deleting_with_purchase), skip stock adjustment.
    - Otherwise, reduce the stock quantity by the item quantity.
    - Always recalculate the Purchase total after deleting an item.
    """
    if getattr(instance, '_deleting_with_purchase', False):
        # Skip adjusting stock if deletion is triggered by Purchase delete
        return

    stock = Stock.objects.filter(product=instance.product).first()
    if stock:
        stock.quantity = max(0, stock.quantity - instance.quantity)
        stock.save(update_fields=['quantity'])

    # Recalculate the purchase total if the purchase still exists
    if instance.purchase_id and Purchase.objects.filter(id=instance.purchase_id).exists():
        recalculate_purchase_total(instance.purchase)


@receiver(pre_delete, sender=Purchase)
def mark_items_before_purchase_delete(sender, instance, **kwargs):
    """
    Before deleting a Purchase, mark all related PurchaseItems to signal
    that their deletion is due to the parent Purchase being deleted.

    This ensures that stock adjustments are NOT duplicated when
    PurchaseItems are individually deleted after the Purchase deletion.
    """
    for item in instance.items.all():
        # Attach a temporary attribute to flag items during deletion
        item._deleting_with_purchase = True
