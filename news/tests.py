from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from news.models import Post


class PostPaginationAPITestCase(APITestCase):
    def setUp(self):
        # Create a sample image for featured_image
        self.dummy_image = SimpleUploadedFile(
            name="test_post.jpg",
            content=b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b",
            content_type="image/gif",
        )

    def _create_posts(self, count, prefix="published", is_published=True):
        posts = []
        for i in range(count):
            posts.append(
                Post.objects.create(
                    title=f"Brand New Post {prefix} {i + 1:02d}",
                    featured_image=self.dummy_image,
                    content=f"<p>Content for post {prefix} {i + 1:02d}</p>",
                    is_published=is_published,
                )
            )
        return posts

    def test_empty_list_returns_paginated_envelope(self):
        resp = self.client.get("/api/v1/news/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("count", resp.data)
        self.assertIn("next", resp.data)
        self.assertIn("previous", resp.data)
        self.assertIn("results", resp.data)

        self.assertEqual(resp.data["count"], 0)
        self.assertIsNone(resp.data["next"])
        self.assertIsNone(resp.data["previous"])
        self.assertEqual(resp.data["results"], [])

    def test_published_posts_returned_with_standard_pagination_and_navigation(self):
        # Create 25 published posts (StandardPagination page_size is 20)
        self._create_posts(25, is_published=True)

        # Page 1
        resp_p1 = self.client.get("/api/v1/news/")
        self.assertEqual(resp_p1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_p1.data["count"], 25)
        self.assertEqual(len(resp_p1.data["results"]), 20)
        self.assertIsNotNone(resp_p1.data["next"])
        self.assertIn("page=2", resp_p1.data["next"])
        self.assertIsNone(resp_p1.data["previous"])

        # Check fields of results
        first_item = resp_p1.data["results"][0]
        self.assertIn("id", first_item)
        self.assertIn("title", first_item)
        self.assertIn("slug", first_item)
        self.assertIn("featured_image", first_item)
        self.assertIn("created_at", first_item)

        # Page 2 navigation
        resp_p2 = self.client.get(resp_p1.data["next"])
        self.assertEqual(resp_p2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp_p2.data["count"], 25)
        self.assertEqual(len(resp_p2.data["results"]), 5)
        self.assertIsNone(resp_p2.data["next"])
        self.assertIsNotNone(resp_p2.data["previous"])

    def test_custom_page_size_query_param(self):
        self._create_posts(12, is_published=True)

        resp = self.client.get("/api/v1/news/?page_size=5")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 12)
        self.assertEqual(len(resp.data["results"]), 5)
        self.assertIn("page=2", resp.data["next"])
        self.assertIn("page_size=5", resp.data["next"])

    def test_unpublished_posts_excluded_from_pagination_count_and_results(self):
        self._create_posts(5, prefix="pub", is_published=True)
        self._create_posts(3, prefix="unpub", is_published=False)

        resp = self.client.get("/api/v1/news/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["count"], 5)
        self.assertEqual(len(resp.data["results"]), 5)

    def test_ordering_is_newest_first(self):
        now = timezone.now()
        from datetime import timedelta

        post1 = Post.objects.create(title="Post 1", featured_image=self.dummy_image, content="C1", is_published=True)
        post1.created_at = now - timedelta(days=2)
        post1.save(update_fields=["created_at"])

        post2 = Post.objects.create(title="Post 2", featured_image=self.dummy_image, content="C2", is_published=True)
        post2.created_at = now - timedelta(days=1)
        post2.save(update_fields=["created_at"])

        post3 = Post.objects.create(title="Post 3", featured_image=self.dummy_image, content="C3", is_published=True)
        post3.created_at = now
        post3.save(update_fields=["created_at"])

        resp = self.client.get("/api/v1/news/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data["results"]
        # Newest post (created last) should be first
        self.assertEqual(results[0]["id"], post3.id)
        self.assertEqual(results[-1]["id"], post1.id)

    def test_detail_view_remains_functional_with_full_content(self):
        post = Post.objects.create(
            title="Detailed News Article",
            featured_image=self.dummy_image,
            content="<p>Full content of the article.</p>",
            is_published=True,
        )

        resp = self.client.get(f"/api/v1/news/{post.slug}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["title"], "Detailed News Article")
        self.assertEqual(resp.data["content"], "<p>Full content of the article.</p>")
        self.assertIn("updated_at", resp.data)
