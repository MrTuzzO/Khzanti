from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_ckeditor_5.exceptions import NoImageException
from django_ckeditor_5.forms import UploadFileForm
from django_ckeditor_5.permissions import check_upload_permission
from django_ckeditor_5.storage_utils import handle_uploaded_file
from PIL import Image


def _image_verify(f):
    try:
        Image.open(f).verify()
    except OSError:
        raise NoImageException
    finally:
        # Image.verify() reads through the stream and leaves the cursor at EOF.
        # Storage backends that don't auto-seek (e.g. Cloudinary's uploader,
        # unlike Django's local FileSystemStorage) would then upload an empty file.
        f.seek(0)


@require_POST
@check_upload_permission
def upload_file(request):
    form = UploadFileForm(request.POST, request.FILES)
    allow_all_file_types = getattr(settings, "CKEDITOR_5_ALLOW_ALL_FILE_TYPES", False)

    if not allow_all_file_types:
        try:
            _image_verify(request.FILES["upload"])
        except NoImageException as ex:
            return JsonResponse({"error": {"message": f"{ex}"}}, status=400)

    if form.is_valid():
        url = handle_uploaded_file(request.FILES["upload"])
        return JsonResponse({"url": url})

    if form.errors["upload"]:
        return JsonResponse({"error": {"message": form.errors["upload"][0]}}, status=400)

    return JsonResponse({"error": {"message": "Invalid form data"}}, status=400)
