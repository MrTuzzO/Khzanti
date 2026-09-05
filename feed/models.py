from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class Privacy(models.TextChoices):
    PUBLIC = "public", _("Public")
    PRIVATE = "private", _("Private")


class Post(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="feed_posts",
    )
    caption = models.TextField(blank=True)
    privacy = models.CharField(
        max_length=20,
        choices=Privacy.choices,
        default=Privacy.PUBLIC,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Post"
        verbose_name_plural = "Posts"
        indexes = [
            models.Index(fields=["user", "privacy", "-created_at"]),
        ]

    def __str__(self):
        return f"Post {self.id} by {self.user_id} ({self.privacy})"


class PostImage(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="images",
    )
    image = models.ImageField(upload_to="feed/posts/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Post Image"
        verbose_name_plural = "Post Images"

    def __str__(self):
        return f"Image for Post {self.post_id}"


class PostRating(models.Model):
    RATING_VALIDATORS = [MinValueValidator(0), MaxValueValidator(10)]

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="ratings",
    )
    rater = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="post_ratings",
    )
    color_harmony = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    trendy = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    overall_matching = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    accessories = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Post Rating"
        verbose_name_plural = "Post Ratings"
        constraints = [
            models.UniqueConstraint(
                fields=["post", "rater"],
                name="unique_rating_per_user_per_post",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "rater"]),
        ]

    def __str__(self):
        return f"Rating {self.id} by {self.rater_id} on Post {self.post_id}"
