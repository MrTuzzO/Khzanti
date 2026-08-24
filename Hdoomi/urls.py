from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
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
    path('api/v1/outfits/', include('outfits.urls')),
    path('api/v1/model3d/', include('model3d.urls')),
    path('api/v1/social/', include('social.urls')),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


def api_handler404(request, exception=None):
    if request.path.startswith("/api/v1/") or request.headers.get("accept") == "application/json":
        return JsonResponse(
            {
                "status": "error",
                "code": 404,
                "message": "The requested resource was not found.",
                "data": None,
            },
            status=404,
            content_type="application/json",
        )
    from django.views.defaults import page_not_found
    return page_not_found(request, exception)


def api_handler500(request):
    if request.path.startswith("/api/v1/") or request.headers.get("accept") == "application/json":
        return JsonResponse(
            {
                "status": "error",
                "code": 500,
                "message": "An unexpected server error occurred.",
                "data": None,
            },
            status=500,
            content_type="application/json",
        )
    from django.views.defaults import server_error
    return server_error(request)


handler404 = "Hdoomi.urls.api_handler404"
handler500 = "Hdoomi.urls.api_handler500"