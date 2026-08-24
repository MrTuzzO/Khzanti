from datetime import timedelta
import io
from unittest.mock import MagicMock, patch
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase


from avatars.models import Avatar
from outfits.models import JobStatus, OutfitJob, TriggerType
from wardrobe.models import Category
from wardrobe_items_ai.models import ItemAnalysis, WardrobeItem

User = get_user_model()


def generate_test_image_bytes():
    buf = io.BytesIO()
    img = Image.new("RGB", (100, 100), color="red")
    img.save(buf, format="PNG")
    return buf.getvalue()


VALID_PNG_BYTES = generate_test_image_bytes()

MOCK_CLOUDINARY_RESPONSE = {
    "public_id": "outfits/result/test_tryon",
    "version": 1234567890,
    "width": 100,
    "height": 100,
    "format": "png",
    "resource_type": "image",
    "created_at": "2026-08-17T00:00:00Z",
    "bytes": 68,
    "type": "upload",
    "url": "http://res.cloudinary.com/test/image/upload/v1234567890/test_tryon.png",
    "secure_url": "https://res.cloudinary.com/test/image/upload/v1234567890/test_tryon.png",
}


@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class TryOnSaveAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tryonuser@example.com",
            name="TryOn User",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)

        self.other_user = User.objects.create_user(
            email="otheruser@example.com",
            name="Other User",
            password="password123",
        )

        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/avatar.png",
            is_saved=True,
        )

        self.category, _ = Category.objects.get_or_create(name="Tops")
        self.dummy_image = SimpleUploadedFile(
            name="top.png",
            content=VALID_PNG_BYTES,
            content_type="image/png",
        )
        self.wardrobe_item = WardrobeItem.objects.create(
            user=self.user,
            category=self.category,
            image=self.dummy_image,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=self.wardrobe_item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/top_bg.png",
            is_saved=False,
        )

    @patch("requests.get")
    def test_manual_tryon_save_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_tryon_result.png",
            is_saved=False,
        )
        job.wardrobe_items.add(self.wardrobe_item)

        # 1. Status check returns fal.ai URL & saved=False
        status_resp = self.client.get(f"/api/v1/outfits/try-on/{job.id}/status/")
        self.assertEqual(status_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp.data["result_image"], "https://v3b.fal.media/manual_tryon_result.png")

        # 2. Save endpoint uploads to Cloudinary
        save_resp = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(save_resp.data["saved"])
        self.assertIn("http", save_resp.data["result_image"])

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

        # 3. Duplicate save call is idempotent
        save_resp_2 = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp_2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_requests_get.call_count, 1)

    @patch("requests.get")
    def test_auto_tryon_save_flow_and_idempotency(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_tryon_result.png",
            is_saved=False,
        )
        job.wardrobe_items.add(self.wardrobe_item)

        # Save AUTO try-on
        save_resp = self.client.post(f"/api/v1/outfits/try-on/{job.id}/save/")
        self.assertEqual(save_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(save_resp.data["saved"])
        self.assertEqual(save_resp.data["trigger_type"], TriggerType.AUTO)

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

    @patch("requests.get")
    def test_calendar_save_now_unsaved_manual_tryon(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/manual_tryon_result.png",
            is_saved=False,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
            "note": "Work outfit",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["saved_date"], "2026-08-25")
        self.assertTrue(response.data["outfit_job"]["saved"])
        self.assertIn("http", response.data["outfit_job"]["result_image"])

        mock_requests_get.assert_called_once()
        job.refresh_from_db()
        self.assertTrue(job.is_saved)

    @patch("requests.get")
    def test_calendar_save_now_unsaved_auto_tryon(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/auto_tryon_result.png",
            is_saved=False,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["outfit_job"]["saved"])
        mock_requests_get.assert_called_once()

    @patch("requests.get")
    def test_calendar_save_now_already_saved_tryon(self, mock_requests_get, mock_cloud):
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/already_saved.png",
            is_saved=True,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        mock_requests_get.assert_not_called()

    @patch("requests.get")
    def test_generation_date_differs_from_calendar_date(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        from datetime import date
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 17),
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/diff_dates.png",
            is_saved=False,
        )

        # Save for August 25
        self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        # Query August 25 calendar -> outfit present
        res_aug_25 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-25&end_date=2026-08-25")
        self.assertEqual(res_aug_25.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_aug_25.data), 1)

        # Query August 17 calendar -> 0 outfits
        res_aug_17 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-17&end_date=2026-08-17")
        self.assertEqual(res_aug_17.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_aug_17.data), 0)

    @patch("requests.get")
    def test_patch_saved_outfit_date(self, mock_requests_get, mock_cloud):
        mock_resp = MagicMock()
        mock_resp.content = VALID_PNG_BYTES
        mock_resp.status_code = 200
        mock_requests_get.return_value = mock_resp

        from datetime import date
        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            scheduled_date=date(2026, 8, 17),
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/patch_test.png",
            is_saved=False,
        )

        post_res = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })
        saved_id = post_res.data["id"]

        # Update date to August 28
        patch_res = self.client.patch(f"/api/v1/outfits/saved/{saved_id}/", {
            "saved_date": "2026-08-28",
        })
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)

        # Verify disappears from August 25 and appears on August 28
        res_25 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-25&end_date=2026-08-25")
        self.assertEqual(len(res_25.data), 0)

        res_28 = self.client.get("/api/v1/outfits/saved/?start_date=2026-08-28&end_date=2026-08-28")
        self.assertEqual(len(res_28.data), 1)

        # Verify OutfitJob.scheduled_date remains unchanged
        job.refresh_from_db()
        self.assertEqual(job.scheduled_date, date(2026, 8, 17))

    def test_unauthorized_outfit_job_calendar_save(self, mock_cloud):
        other_job = OutfitJob.objects.create(
            user=self.other_user,
            avatar=self.avatar,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/other.png",
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": other_job.id,
            "saved_date": "2026-08-25",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_incomplete_outfit_job_calendar_save_rejection(self, mock_cloud):
        pending_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            status=JobStatus.PENDING,
        )

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": pending_job.id,
            "saved_date": "2026-08-25",
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("requests.get")
    def test_cloudinary_failure_prevents_saved_outfit_creation(self, mock_requests_get, mock_cloud):
        mock_requests_get.side_effect = Exception("Cloudinary upload failed")

        job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/fail.png",
            is_saved=False,
        )

        from outfits.models import SavedOutfit
        count_before = SavedOutfit.objects.filter(user=self.user).count()

        response = self.client.post("/api/v1/outfits/saved/", {
            "outfit_job_id": job.id,
            "saved_date": "2026-08-25",
        })

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        count_after = SavedOutfit.objects.filter(user=self.user).count()
        self.assertEqual(count_before, count_after)

        job.refresh_from_db()
        self.assertFalse(job.is_saved)


class OneMonthOutfitHistoryAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="user1month@example.com",
            name="1Month User",
            password="password123",
        )
        self.other_user = User.objects.create_user(
            email="other1month@example.com",
            name="Other User",
            password="password123",
        )
        self.avatar = Avatar.objects.create(
            user=self.user,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            is_saved=True,
        )

    def test_unauthenticated_request_rejected(self):
        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_history_returns_200_empty_list(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_authenticated_user_receives_outfits_within_last_30_days_and_excludes_older_and_other_users(self):
        self.client.force_authenticate(user=self.user)

        now = timezone.now()

        # Job 1: Recent job created 2 days ago
        recent_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=recent_job.pk).update(created_at=now - timedelta(days=2))

        # Job 2: Recent job created today
        today_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=today_job.pk).update(created_at=now)

        # Job 3: Old job created 35 days ago (older than 30 days)
        old_job = OutfitJob.objects.create(
            user=self.user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=old_job.pk).update(created_at=now - timedelta(days=35))

        # Job 4: Other user's job created today
        other_user_job = OutfitJob.objects.create(
            user=self.other_user,
            avatar=self.avatar,
            trigger_type=TriggerType.MANUAL,
            status=JobStatus.DONE,
        )
        OutfitJob.objects.filter(pk=other_user_job.pk).update(created_at=now)

        response = self.client.get("/api/v1/outfits/1-months/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        retrieved_ids = [item["id"] for item in response.data]
        self.assertEqual(len(retrieved_ids), 2)
        self.assertEqual(retrieved_ids, [today_job.id, recent_job.id])
        self.assertNotIn(old_job.id, retrieved_ids)
        self.assertNotIn(other_user_job.id, retrieved_ids)


import json
from django.conf import settings
from accounts.models import CustomerProfile, Gender, Aesthetic
from outfits.services import (
    filter_eligible_wardrobe_items,
    validate_outfit_composition,
    validate_outfit_semantic_suitability,
    analyze_garment_semantics,
    categorize_item_semantics,
    select_auto_tryon_assets,
    select_auto_outfit_combination,
    get_profile_constraints,
    resolve_tryon_avatar,
    get_or_create_today_auto_job,
)



@patch("cloudinary.uploader.upload", return_value=MOCK_CLOUDINARY_RESPONSE)
class DailyOutfitPipelineRecommendationAPITestCase(APITestCase):
    def setUp(self):

        self.male_user = User.objects.create_user(
            email="maleuser@example.com",
            name="Male User",
            password="Password123!",
        )
        self.male_profile, _ = CustomerProfile.objects.get_or_create(
            user=self.male_user,
            defaults={
                "gender": Gender.MALE,
                "age": 25,
                "country": "Bangladesh",
                "body_type": "average",
                "height": 175,
            }
        )

        self.female_user = User.objects.create_user(
            email="femaleuser@example.com",
            name="Female User",
            password="Password123!",
        )
        self.female_profile, _ = CustomerProfile.objects.get_or_create(
            user=self.female_user,
            defaults={
                "gender": Gender.FEMALE,
                "age": 24,
                "country": "Bangladesh",
                "body_type": "slim",
                "height": 165,
            }
        )
        minimalist_aesthetic, _ = Aesthetic.objects.get_or_create(name="Minimalist")
        self.female_profile.aesthetics.add(minimalist_aesthetic)

        self.cat_tops, _ = Category.objects.get_or_create(name="Tops & T-Shirts")
        self.cat_bottoms, _ = Category.objects.get_or_create(name="Bottoms & Pants")
        self.cat_dresses, _ = Category.objects.get_or_create(name="Dresses")
        self.cat_kaftan, _ = Category.objects.get_or_create(name="Kaftan")
        self.cat_outerwear, _ = Category.objects.get_or_create(name="Outerwear")
        self.cat_shoes, _ = Category.objects.get_or_create(name="Shoes")

    def _create_item(self, user, category, description="", color=""):
        img = SimpleUploadedFile("item.jpg", b"image_data", content_type="image/jpeg")
        item = WardrobeItem.objects.create(
            user=user,
            category=category,
            image=img,
        )
        ItemAnalysis.objects.create(
            wardrobe_item=item,
            status=ItemAnalysis.JobStatus.DONE,
            fal_cdn_url=f"https://v3b.fal.media/item_{item.id}.png",
            description=description,
            color=color,
            is_saved=False,
        )
        return item


    def test_profile_constraints_resolved_from_customer_profile(self, *args):
        constraints = get_profile_constraints(self.female_user)
        self.assertEqual(constraints["gender"], "female")
        self.assertIn("Minimalist", constraints["aesthetics"])

    def test_male_user_eligible_filtering_excludes_dresses(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)

        all_items = [top, bottom, dress]
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items(all_items, constraints)

        self.assertIn(top, eligible)
        self.assertIn(bottom, eligible)
        self.assertNotIn(dress, eligible)

    def test_outfit_composition_validation_rules(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.female_user, self.cat_dresses)
        shoes = self._create_item(self.male_user, self.cat_shoes)

        # 1. Top + Bottom + Shoes -> Valid
        valid_2pc, _ = validate_outfit_composition([top, bottom, shoes])
        self.assertTrue(valid_2pc)

        # 2. Dress + Shoes -> Valid
        valid_dress, _ = validate_outfit_composition([dress, shoes])
        self.assertTrue(valid_dress)

        # 3. Dress + Bottom -> Invalid
        invalid_dress_pants, _ = validate_outfit_composition([dress, bottom])
        self.assertFalse(invalid_dress_pants)

        # 4. Top + Top -> Invalid
        invalid_top_top, _ = validate_outfit_composition([top, top])
        self.assertFalse(invalid_top_top)

        # 5. Shoes only -> Invalid
        invalid_shoes_only, _ = validate_outfit_composition([shoes])
        self.assertFalse(invalid_shoes_only)

        # 6. Top alone -> Valid (Incomplete wardrobe support)
        valid_top_alone, _ = validate_outfit_composition([top])
        self.assertTrue(valid_top_alone)

        # 7. Bottom alone -> Valid (Incomplete wardrobe support)
        valid_bottom_alone, _ = validate_outfit_composition([bottom])
        self.assertTrue(valid_bottom_alone)

    def test_incomplete_wardrobe_top_alone_and_top_jacket_selection(self, *args):
        top = self._create_item(self.male_user, self.cat_tops, description="Blue Oxford shirt")
        today_date = timezone.now().date()

        # Single top in wardrobe -> Generates Top alone without error
        _, selected_items = select_auto_tryon_assets(self.male_user, today_date)
        self.assertEqual(selected_items, [top])

        # Top + Outerwear in wardrobe (no pants/shoes) -> Generates Top + Outerwear
        jacket = self._create_item(self.male_user, self.cat_outerwear, description="Light blue blazer")
        _, selected_items_2 = select_auto_tryon_assets(self.male_user, today_date)
        self.assertIn(top, selected_items_2)
        self.assertIn(jacket, selected_items_2)

    def test_incomplete_wardrobe_dress_alone_selection(self, *args):
        dress = self._create_item(self.female_user, self.cat_dresses, description="Floral summer dress")
        today_date = timezone.now().date()

        # Single dress in wardrobe -> Generates Dress alone without requiring shoes/accessories
        _, selected_items = select_auto_tryon_assets(self.female_user, today_date)
        self.assertEqual(selected_items, [dress])

    def test_ambiguous_traditional_garment_allowed(self, *args):
        ambiguous_kaftan = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="Traditional white kaftan garment.",
            color="white"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([ambiguous_kaftan], constraints)
        self.assertIn(ambiguous_kaftan, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([ambiguous_kaftan], constraints)
        self.assertTrue(is_sem_valid)


    def test_male_user_feminine_kaftan_dress_rejected_semantically(self, *args):
        kaftan_dress = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A long, flowing beige Kaftan dress with short sleeves and a draped bodice featuring a decorative brooch at the waist.",
            color="beige"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([kaftan_dress], constraints)
        self.assertNotIn(kaftan_dress, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([kaftan_dress], constraints)
        self.assertFalse(is_sem_valid)

    def test_male_user_men_traditional_thobe_allowed(self, *args):
        men_thobe = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A classic white men's thobe / kaftan with mandarin collar.",
            color="white"
        )
        constraints = get_profile_constraints(self.male_user)
        eligible = filter_eligible_wardrobe_items([men_thobe], constraints)
        self.assertIn(men_thobe, eligible)

        is_sem_valid, _ = validate_outfit_semantic_suitability([men_thobe], constraints)
        self.assertTrue(is_sem_valid)

    def test_exact_outfit_job_198_scenario_rejection(self, *args):
        # Recreating exact scenario of OutfitJob 198 (feminine kaftan dress 78 + heavy trench coat 72 + white sneakers 21 for male in Bangladesh summer)
        kaftan_dress_78 = self._create_item(
            self.male_user,
            self.cat_kaftan,
            description="A long, flowing beige Kaftan dress with short sleeves and a draped bodice featuring a decorative brooch at the waist.",
            color="beige"
        )
        trench_coat_72 = self._create_item(
            self.male_user,
            self.cat_outerwear,
            description="A classic beige trench coat with double-breasted buttons, epaulets, and a waist belt, worn over a suit.",
            color="beige"
        )
        sneakers_21 = self._create_item(
            self.male_user,
            self.cat_shoes,
            description="A white low-top sneaker featuring a classic design.",
            color="white"
        )

        constraints = get_profile_constraints(self.male_user)
        today_date = timezone.now().date()

        is_sem_valid, _ = validate_outfit_semantic_suitability([kaftan_dress_78, trench_coat_72, sneakers_21], constraints, today_date)
        self.assertFalse(is_sem_valid)

    def test_fallback_selection_does_not_blindly_pick_all_categories(self, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)
        shoes = self._create_item(self.male_user, self.cat_shoes)

        today_date = timezone.now().date()
        _, selected_items = select_auto_tryon_assets(self.male_user, today_date)

        self.assertNotIn(dress, selected_items)
        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)

    def test_female_user_can_select_dress_outfit(self, *args):
        dress = self._create_item(self.female_user, self.cat_dresses)
        shoes = self._create_item(self.female_user, self.cat_shoes)

        today_date = timezone.now().date()
        _, selected_items = select_auto_tryon_assets(self.female_user, today_date)

        self.assertIn(dress, selected_items)
        self.assertIn(shoes, selected_items)

    def test_fullbody_garment_plus_outerwear_valid_composition(self, *args):
        outerwear_cat, _ = Category.objects.get_or_create(name="Outerwear")
        dress = self._create_item(self.female_user, self.cat_dresses)
        jacket = self._create_item(self.female_user, outerwear_cat, description="Light stylish denim jacket")
        bottom = self._create_item(self.female_user, self.cat_bottoms)

        # Full-body + Outerwear -> Valid
        valid_fb_outer, _ = validate_outfit_composition([dress, jacket])
        self.assertTrue(valid_fb_outer)

        # Full-body + Bottom -> Invalid
        invalid_fb_bottom, _ = validate_outfit_composition([dress, bottom])
        self.assertFalse(invalid_fb_bottom)

    @patch("outfits.services.OpenAI")
    def test_openai_invalid_category_combination_rejected_by_backend(self, mock_openai, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        dress = self._create_item(self.male_user, self.cat_dresses)

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "selected_item_ids": [top.id, bottom.id, dress.id],
                "reasoning": {"title": "Test Look", "subtitle": "Test"}
            })))
        ]

        today_date = timezone.now().date()
        with patch.object(settings, "OPENAI_API_KEY", "dummy_key"):
            _, selected_items, _ = select_auto_outfit_combination(self.male_user, today_date)

        self.assertNotIn(dress, selected_items)
        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)

    @patch("outfits.services.OpenAI")
    def test_openai_failure_uses_valid_fallback(self, mock_openai, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)

        mock_instance = mock_openai.return_value
        mock_instance.chat.completions.create.side_effect = Exception("OpenAI service unavailable")

        today_date = timezone.now().date()
        with patch.object(settings, "OPENAI_API_KEY", "dummy_key"):
            _, selected_items, reasoning = select_auto_outfit_combination(self.male_user, today_date)

        self.assertIn(top, selected_items)
        self.assertIn(bottom, selected_items)
        self.assertIn("Daily Outfit Recommendation", reasoning["title"])

    def test_no_valid_composition_returns_failed_job_response(self, *args):
        # Male user only has a dress (0 eligible items)
        dress = self._create_item(self.male_user, self.cat_dresses)

        job = get_or_create_today_auto_job(self.male_user)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn("No completed wardrobe items", job.error_message)

    @patch("outfits.services.submit_try_on_job")
    def test_today_outfit_api_endpoint_lazy_generation(self, mock_submit, *args):
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)
        avatar = resolve_tryon_avatar(self.male_user)

        self.client.force_authenticate(user=self.male_user)
        response = self.client.get("/api/v1/outfits/today/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "success")
        self.assertIn("id", response.data["data"])

    @patch("outfits.services.submit_try_on_job")
    def test_failed_today_auto_job_is_retried_on_get_today(self, mock_submit, *args):
        today_date = timezone.now().date()
        avatar = resolve_tryon_avatar(self.male_user)
        
        # 1. Create a stale failed job for today
        stale_failed_job = OutfitJob.objects.create(
            user=self.male_user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.FAILED,
            error_message="Stale failure from earlier"
        )
        
        # 2. Add valid completed wardrobe items to user's wardrobe
        top = self._create_item(self.male_user, self.cat_tops)
        bottom = self._create_item(self.male_user, self.cat_bottoms)

        # 3. GET /today/ should retry generation and create a new active job
        new_job = get_or_create_today_auto_job(self.male_user)
        self.assertNotEqual(new_job.id, stale_failed_job.id)
        self.assertNotEqual(new_job.status, JobStatus.FAILED)
        self.assertIn(top, new_job.wardrobe_items.all())
        self.assertFalse(OutfitJob.objects.filter(id=stale_failed_job.id).exists())

    @patch("outfits.services.submit_try_on_job")
    def test_done_today_auto_job_is_cached(self, mock_submit, *args):
        today_date = timezone.now().date()
        avatar = resolve_tryon_avatar(self.male_user)
        
        # 1. Create a DONE job for today
        done_job = OutfitJob.objects.create(
            user=self.male_user,
            avatar=avatar,
            scheduled_date=today_date,
            trigger_type=TriggerType.AUTO,
            status=JobStatus.DONE,
            result_image="https://res.cloudinary.com/demo/image.jpg"
        )
        
        # 2. GET /today/ returns the cached DONE job without creating a new one
        retrieved_job = get_or_create_today_auto_job(self.male_user)
        self.assertEqual(retrieved_job.id, done_job.id)
        self.assertEqual(retrieved_job.status, JobStatus.DONE)

