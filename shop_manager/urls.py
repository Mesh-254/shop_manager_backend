from rest_framework.routers import DefaultRouter
from django.urls import path, include
from shop_manager import views


# Create a router and register our viewset with it.
router = DefaultRouter()

router.register(r'subscriptionplans', views.SubscriptionPlanViewSet, basename='subscriptionplan')
router.register(r'shops', views.ShopViewSet, basename='shop')
router.register(r'categories', views.CategoryViewSet, basename='category')
router.register(r'suppliers', views.SupplierViewSet, basename='supplier')
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'stocks', views.StockViewSet, basename='stock')
router.register(r'purchases', views.PurchaseViewSet, basename='purchase')
router.register(r'purchaseitems', views.PurchaseItemsViewSet, basename='purchaseitem')
router.register(r'sales', views.SaleViewSet, basename='sale')
router.register(r'saleitems', views.SaleItemsViewSet, basename='saleitem')


urlpatterns = [
    path('', include(router.urls)),
]
