from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core.ckeditor_uploads import upload_file as ckeditor_upload_file

def health_check(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', health_check, name='health-check'),
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
    path('api/v1/feed/', include('feed.urls')),
    path('api/v1/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/v1/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


from core.middleware import is_api_request


def api_handler400(request, exception=None):
    if is_api_request(request):
        return JsonResponse({"detail": "Bad request."}, status=400)
    from django.views.defaults import bad_request
    return bad_request(request, exception)


def api_handler403(request, exception=None):
    if is_api_request(request):
        return JsonResponse({"detail": "Permission denied."}, status=403)
    from django.views.defaults import permission_denied
    return permission_denied(request, exception)


def api_handler404(request, exception=None):
    if is_api_request(request):
        return JsonResponse({"detail": "Not found."}, status=404)
    from django.views.defaults import page_not_found
    return page_not_found(request, exception)


def api_handler500(request):
    if is_api_request(request):
        return JsonResponse({"detail": "An unexpected server error occurred."}, status=500)
    from django.views.defaults import server_error
    return server_error(request)


handler400 = "Khzanti.urls.api_handler400"
handler403 = "Khzanti.urls.api_handler403"
handler404 = "Khzanti.urls.api_handler404"
handler500 = "Khzanti.urls.api_handler500"
