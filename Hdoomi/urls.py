from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core.ckeditor_uploads import upload_file as ckeditor_upload_file

urlpatterns = [
    path('admin/', admin.site.urls),
    path('ckeditor5/image_upload/', ckeditor_upload_file, name='ck_editor_5_upload_file'),
    path('api/v1/auth/', include('accounts.urls')),
    path('api/v1/', include('core.urls')),
    path('api/v1/wardrobe/', include('wardrobe.urls')),
    path('api/v1/news/', include('news.urls')),
    path('api/v1/avatars/', include('avatars.urls')),
    path('api/v1/wardrobe-items-ai/', include('wardrobe_items_ai.urls')),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)