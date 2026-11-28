from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from feed.models import Post, PostReport
from social.models import Block, Follow, UserReport

User = get_user_model()


class ReportAndBlockTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(email="a@x.com", username="alice", password="pw12345!", name="Alice")
        self.bob = User.objects.create_user(email="b@x.com", username="bob", password="pw12345!", name="Bob")
        self.client.force_authenticate(self.alice)

    def test_block_removes_follows_and_hides_user(self):
        Follow.objects.create(follower=self.alice, following=self.bob)
        Follow.objects.create(follower=self.bob, following=self.alice)

        res = self.client.post(reverse("social:block-toggle", args=["bob"]))
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Block.objects.filter(blocker=self.alice, blocked=self.bob).exists())
        self.assertEqual(Follow.objects.count(), 0)

        self.assertEqual(self.client.get(reverse("social:public-profile", args=["bob"])).status_code, 404)
        search = self.client.get(reverse("social:user-search"), {"q": "bob"})
        self.assertEqual(search.data["count"] if "count" in search.data else len(search.data), 0)

        # Hidden the other way round too.
        self.client.force_authenticate(self.bob)
        self.assertEqual(self.client.get(reverse("social:public-profile", args=["alice"])).status_code, 404)

    def test_blocked_list_and_unblock(self):
        self.client.post(reverse("social:block-toggle", args=["bob"]))
        res = self.client.get(reverse("social:blocked-list"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["results"][0]["username"], "bob")

        self.assertEqual(self.client.delete(reverse("social:block-toggle", args=["bob"])).status_code, 200)
        self.assertFalse(Block.objects.exists())

    def test_cannot_block_or_report_self(self):
        self.assertEqual(self.client.post(reverse("social:block-toggle", args=["alice"])).status_code, 400)
        res = self.client.post(reverse("social:user-report", args=["alice"]), {"reason": "spam"})
        self.assertEqual(res.status_code, 400)

    def test_report_user(self):
        url = reverse("social:user-report", args=["bob"])
        res = self.client.post(url, {"reason": "spam", "details": "bad"})
        self.assertEqual(res.status_code, 201)
        # Reporting again updates rather than duplicates.
        res = self.client.post(url, {"reason": "harassment"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(UserReport.objects.count(), 1)
        self.assertEqual(UserReport.objects.get().reason, "harassment")
        self.assertEqual(self.client.post(url, {"reason": "nope"}).status_code, 400)

    def test_report_post_and_block_hides_posts(self):
        post = Post.objects.create(user=self.bob, caption="hi", privacy="public")
        url = reverse("post-report", args=[post.id])
        self.assertEqual(self.client.post(url, {"reason": "inappropriate"}).status_code, 201)
        self.assertEqual(PostReport.objects.count(), 1)

        own = Post.objects.create(user=self.alice, caption="mine", privacy="public")
        self.assertEqual(
            self.client.post(reverse("post-report", args=[own.id]), {"reason": "spam"}).status_code, 400
        )

        self.client.post(reverse("social:block-toggle", args=["bob"]))
        res = self.client.get(reverse("post-list"), {"username": "bob"})
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        self.assertEqual(len(results), 0)
        self.assertEqual(self.client.post(url, {"reason": "spam"}).status_code, 404)

    def test_report_outfit(self):
        from outfits.models import OutfitJob, SavedOutfit
        from feed.models import OutfitReport
        from avatars.models import Avatar
        avatar = Avatar.objects.create(user=self.bob, style=Avatar.Style.REALISTIC, status=Avatar.JobStatus.DONE)
        job = OutfitJob.objects.create(user=self.bob, avatar=avatar)
        shared = SavedOutfit.objects.create(user=self.bob, outfit_job=job, date="2026-01-01", is_shared=True)
        url = reverse("outfit-report", args=[shared.id])
        self.assertEqual(self.client.post(url, {"reason": "spam"}).status_code, 201)
        self.assertEqual(self.client.post(url, {"reason": "hate"}).status_code, 200)
        self.assertEqual(OutfitReport.objects.count(), 1)

        private_job = OutfitJob.objects.create(user=self.bob, avatar=avatar)
        private = SavedOutfit.objects.create(user=self.bob, outfit_job=private_job, date="2026-01-02")
        self.assertEqual(
            self.client.post(reverse("outfit-report", args=[private.id]), {"reason": "spam"}).status_code, 404
        )
