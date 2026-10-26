from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from avatars.models import Avatar
from feed.models import Post, PostImage, PostRating
from outfits.models import DailyOutfitSelection, JobStatus, OutfitJob, OutfitRating, SavedOutfit
from social.models import Follow

User = get_user_model()


class FriendsStyleAPITestCase(APITestCase):
    def setUp(self):
        # Authenticated viewer
        self.user = User.objects.create_user(
            email="viewer@example.com",
            name="Viewer User",
            password="password123",
            username="viewer",
        )
        # Friend 1
        self.friend1 = User.objects.create_user(
            email="friend1@example.com",
            name="Alice Wonder",
            password="password123",
            username="alice",
        )
        # Friend 2
        self.friend2 = User.objects.create_user(
            email="friend2@example.com",
            name="Bob Builder",
            password="password123",
            username="bob",
        )
        # Non-friend
        self.stranger = User.objects.create_user(
            email="stranger@example.com",
            name="Stranger Danger",
            password="password123",
            username="stranger",
        )

        # Viewer follows Alice and Bob
        Follow.objects.create(follower=self.user, following=self.friend1)
        Follow.objects.create(follower=self.user, following=self.friend2)

        # Avatars
        self.avatar_alice = Avatar.objects.create(
            user=self.friend1,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/alice_avatar.png",
        )
        self.avatar_bob = Avatar.objects.create(
            user=self.friend2,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/bob_avatar.png",
        )

        self.today = timezone.now().date()
        self.yesterday = self.today - timedelta(days=1)

        # Alice's Outfit Job (DONE) for today
        self.job_alice_today = OutfitJob.objects.create(
            user=self.friend1,
            avatar=self.avatar_alice,
            status=JobStatus.DONE,
            scheduled_date=self.today,
            fal_cdn_url="https://v3b.fal.media/alice_outfit_today.png",
            reasoning_title="Summer Chic",
            reasoning_subtitle="Light linen and denim",
            reasoning_note="A relaxed outfit perfect for sunny days.",
        )
        # Alice shares this outfit
        self.saved_alice_today = SavedOutfit.objects.create(
            user=self.friend1,
            outfit_job=self.job_alice_today,
            date=self.today,
            note="Loving this summer fit!",
            is_shared=True,
        )

        # Alice's Post for today
        self.post_alice_today = Post.objects.create(
            user=self.friend1,
            caption="Good morning world! Today's mood is bright.",
            privacy="public",
        )

        # Bob's Post for today
        self.post_bob_today = Post.objects.create(
            user=self.friend2,
            caption="Casual Friday vibes.\nKeep it simple.",
            privacy="public",
        )

        # Stranger's Post for today (should not appear in viewer's feed)
        self.post_stranger_today = Post.objects.create(
            user=self.stranger,
            caption="Stranger's public post",
            privacy="public",
        )

    def test_friends_style_requires_authentication(self):
        url = "/api/v1/feed/friends-style/"
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_friends_style_empty_when_no_following(self):
        user_no_friends = User.objects.create_user(
            email="lonely@example.com",
            name="Lonely User",
            password="password123",
            username="lonely",
        )
        self.client.force_authenticate(user=user_no_friends)
        res = self.client.get("/api/v1/feed/friends-style/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("count"), 0)
        self.assertEqual(res.data.get("results"), [])

    def test_friends_style_single_api_contains_both_outfit_and_post(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.get(f"/api/v1/feed/friends-style/?date={self.today.isoformat()}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        results = res.data.get("results", [])
        self.assertGreaterEqual(len(results), 3)

        # Extract style_types
        style_types = [item["style_type"] for item in results]
        self.assertIn("Outfit", style_types)
        self.assertIn("Post", style_types)

        # Verify outfit item fields
        outfit_item = next(item for item in results if item["style_type"] == "Outfit")
        self.assertEqual(outfit_item["friend_name"], "Alice Wonder")
        self.assertEqual(outfit_item["friend_username"], "alice")
        self.assertEqual(outfit_item["outfit_image"], "https://v3b.fal.media/alice_outfit_today.png")
        self.assertEqual(outfit_item["reasoning"]["title"], "Summer Chic")
        self.assertIn("relaxed outfit", outfit_item["reasoning"]["note"])
        self.assertEqual(outfit_item["reasoning"]["subtitle"], "Light linen and denim")
        self.assertEqual(outfit_item["outfit_id"], self.job_alice_today.id)
        self.assertEqual(outfit_item["id"], self.saved_alice_today.id)
        self.assertFalse(outfit_item["is_saved_by_viewer"])
        self.assertIn("ratings", outfit_item)

        # Verify post item fields
        post_item = next(item for item in results if item["style_type"] == "Post" and item["friend_username"] == "bob")
        self.assertEqual(post_item["friend_name"], "Bob Builder")
        self.assertEqual(post_item["friend_username"], "bob")
        self.assertIn("Casual Friday", post_item["caption"])
        self.assertEqual(post_item["post_id"], self.post_bob_today.id)
        self.assertIn("ratings", post_item)

        # Ensure stranger's post is NOT present
        stranger_post_ids = [item["id"] for item in results if item["style_type"] == "Post"]
        self.assertNotIn(self.post_stranger_today.id, stranger_post_ids)

    def test_friends_style_style_type_filter(self):
        self.client.force_authenticate(user=self.user)

        # Outfit only
        res_outfits = self.client.get(f"/api/v1/feed/friends-style/?date={self.today.isoformat()}&style_type=Outfit")
        self.assertEqual(res_outfits.status_code, status.HTTP_200_OK)
        results = res_outfits.data.get("results", [])
        self.assertTrue(all(item["style_type"] == "Outfit" for item in results))
        self.assertGreaterEqual(len(results), 1)

        # Post only
        res_posts = self.client.get(f"/api/v1/feed/friends-style/?date={self.today.isoformat()}&style_type=Post")
        self.assertEqual(res_posts.status_code, status.HTTP_200_OK)
        results_post = res_posts.data.get("results", [])
        self.assertTrue(all(item["style_type"] == "Post" for item in results_post))
        self.assertGreaterEqual(len(results_post), 2)

    def test_friends_style_date_filtering(self):
        self.client.force_authenticate(user=self.user)

        # Bob's yesterday outfit
        job_bob_yesterday = OutfitJob.objects.create(
            user=self.friend2,
            avatar=self.avatar_bob,
            status=JobStatus.DONE,
            scheduled_date=self.yesterday,
            fal_cdn_url="https://v3b.fal.media/bob_yesterday.png",
            reasoning_title="Vintage Classic",
        )
        SavedOutfit.objects.create(
            user=self.friend2,
            outfit_job=job_bob_yesterday,
            date=self.yesterday,
            is_shared=True,
        )

        # Query yesterday
        res_yesterday = self.client.get(f"/api/v1/feed/friends-style/?date={self.yesterday.isoformat()}")
        self.assertEqual(res_yesterday.status_code, status.HTTP_200_OK)
        results = res_yesterday.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["reasoning"]["title"], "Vintage Classic")

        # Query empty date
        far_future = (self.today + timedelta(days=100)).isoformat()
        res_empty = self.client.get(f"/api/v1/feed/friends-style/?date={far_future}")
        self.assertEqual(res_empty.status_code, status.HTTP_200_OK)
        self.assertEqual(res_empty.data.get("count"), 0)

    def test_friends_style_private_outfits_and_posts_excluded(self):
        self.client.force_authenticate(user=self.user)

        # Bob's private outfit today
        job_bob_private = OutfitJob.objects.create(
            user=self.friend2,
            avatar=self.avatar_bob,
            status=JobStatus.DONE,
            scheduled_date=self.today,
            fal_cdn_url="https://v3b.fal.media/bob_private.png",
        )
        SavedOutfit.objects.create(
            user=self.friend2,
            outfit_job=job_bob_private,
            date=self.today,
            is_shared=False,  # PRIVATE
        )

        # Alice's private post today
        Post.objects.create(
            user=self.friend1,
            caption="Secret post for my eyes only",
            privacy="private",
        )

        res = self.client.get(f"/api/v1/feed/friends-style/?date={self.today.isoformat()}")
        results = res.data.get("results", [])

        # Check private outfit is excluded
        outfit_ids = [item.get("outfit_id") for item in results if item["style_type"] == "Outfit"]
        self.assertNotIn(job_bob_private.id, outfit_ids)

        # Check private post is excluded
        post_captions = [item.get("caption") for item in results if item["style_type"] == "Post"]
        self.assertNotIn("Secret post for my eyes only", post_captions)

    def test_friends_style_daily_outfit_selection_included(self):
        self.client.force_authenticate(user=self.user)

        # Bob selects an outfit for today via DailyOutfitSelection
        job_bob_selected = OutfitJob.objects.create(
            user=self.friend2,
            avatar=self.avatar_bob,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/bob_selected.png",
            reasoning_title="Daily Pick",
        )
        # Shared saved outfit exists
        SavedOutfit.objects.create(
            user=self.friend2,
            outfit_job=job_bob_selected,
            date=self.yesterday,  # saved originally yesterday
            is_shared=True,
        )
        # But selected for today
        DailyOutfitSelection.objects.create(
            user=self.friend2,
            date=self.today,
            outfit_job=job_bob_selected,
        )

        res = self.client.get(f"/api/v1/feed/friends-style/?date={self.today.isoformat()}&style_type=Outfit")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", [])
        outfit_ids = [item["outfit_id"] for item in results]
        self.assertIn(job_bob_selected.id, outfit_ids)


class SaveFriendOutfitAPITestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="viewer2@example.com",
            name="Viewer User 2",
            password="password123",
            username="viewer2",
        )
        self.friend = User.objects.create_user(
            email="friend3@example.com",
            name="Chloe Style",
            password="password123",
            username="chloe",
        )
        Follow.objects.create(follower=self.user, following=self.friend)

        self.avatar_chloe = Avatar.objects.create(
            user=self.friend,
            style=Avatar.Style.REALISTIC,
            status=Avatar.JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/chloe_avatar.png",
        )

        self.job_done = OutfitJob.objects.create(
            user=self.friend,
            avatar=self.avatar_chloe,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/chloe_outfit.png",
            reasoning_title="Street Style",
        )
        self.saved_public = SavedOutfit.objects.create(
            user=self.friend,
            outfit_job=self.job_done,
            date=timezone.now().date(),
            is_shared=True,
        )

    def test_save_friend_outfit_requires_auth(self):
        url = f"/api/v1/feed/friends-style/save/{self.job_done.id}/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_save_friend_outfit_success(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v1/feed/friends-style/save/{self.job_done.id}/"

        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data.get("is_saved"))
        self.assertEqual(res.data.get("outfit_id"), self.job_done.id)

        # Verify SavedOutfit created for viewer
        self.assertTrue(
            SavedOutfit.objects.filter(user=self.user, outfit_job=self.job_done).exists()
        )

        # Verify that is_saved_by_viewer is now True in friends-style feed
        feed_res = self.client.get("/api/v1/feed/friends-style/")
        outfit_items = [i for i in feed_res.data["results"] if i["outfit_id"] == self.job_done.id]
        self.assertEqual(len(outfit_items), 1)
        self.assertTrue(outfit_items[0]["is_saved_by_viewer"])

    def test_save_friend_outfit_already_saved(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v1/feed/friends-style/save/{self.job_done.id}/"

        # First save
        res1 = self.client.post(url)
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Second save (idempotent)
        res2 = self.client.post(url)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertTrue(res2.data.get("is_saved"))
        self.assertIn("already saved", res2.data.get("message"))

    def test_save_friend_outfit_delete(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v1/feed/friends-style/save/{self.job_done.id}/"

        # POST to save
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data.get("is_saved"))

        # DELETE to unsave
        res_del = self.client.delete(url)
        self.assertEqual(res_del.status_code, status.HTTP_200_OK)
        self.assertFalse(res_del.data.get("is_saved"))
        self.assertFalse(
            SavedOutfit.objects.filter(user=self.user, outfit_job=self.job_done).exists()
        )

    def test_save_friend_outfit_toggle(self):
        self.client.force_authenticate(user=self.user)
        url = f"/api/v1/feed/friends-style/save/{self.job_done.id}/"

        # 1. Toggle ON -> should save
        res_on = self.client.post(url, {"action": "toggle"})
        self.assertEqual(res_on.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res_on.data.get("is_saved"))

        # 2. Toggle OFF -> should unsave
        res_off = self.client.post(url, {"action": "toggle"})
        self.assertEqual(res_off.status_code, status.HTTP_200_OK)
        self.assertFalse(res_off.data.get("is_saved"))

    def test_save_friend_outfit_via_saved_outfit_id(self):
        self.client.force_authenticate(user=self.user)
        # Passing friend's saved_outfit_id as pk
        url = f"/api/v1/feed/friends-style/save/{self.saved_public.id}/"

        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertTrue(res.data.get("is_saved"))

    def test_save_friend_outfit_private_forbidden(self):
        self.client.force_authenticate(user=self.user)

        job_private = OutfitJob.objects.create(
            user=self.friend,
            avatar=self.avatar_chloe,
            status=JobStatus.DONE,
            fal_cdn_url="https://v3b.fal.media/private.png",
        )
        SavedOutfit.objects.create(
            user=self.friend,
            outfit_job=job_private,
            date=timezone.now().date(),
            is_shared=False,  # Private
        )

        url = f"/api/v1/feed/friends-style/save/{job_private.id}/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_save_friend_outfit_incomplete_rejected(self):
        self.client.force_authenticate(user=self.user)

        job_pending = OutfitJob.objects.create(
            user=self.friend,
            avatar=self.avatar_chloe,
            status=JobStatus.PENDING,
        )
        SavedOutfit.objects.create(
            user=self.friend,
            outfit_job=job_pending,
            date=timezone.now().date(),
            is_shared=True,
        )

        url = f"/api/v1/feed/friends-style/save/{job_pending.id}/"
        res = self.client.post(url)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
