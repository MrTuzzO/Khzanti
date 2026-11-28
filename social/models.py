from django.conf import settings
from django.db import models


class Follow(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following",  # follow-edges where this user is the follower
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="followers",  # follow-edges where this user is being followed
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["follower", "following"], name="unique_follow_edge"),
            models.CheckConstraint(condition=~models.Q(follower=models.F("following")), name="no_self_follow"),
        ]
        indexes = [
            models.Index(fields=["following", "-created_at"]),
            models.Index(fields=["follower", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.follower_id} -> {self.following_id}"


class ReportReason(models.TextChoices):
    SPAM = "spam", "Spam"
    HARASSMENT = "harassment", "Harassment or bullying"
    INAPPROPRIATE = "inappropriate", "Inappropriate or explicit content"
    HATE = "hate", "Hate speech"
    IMPERSONATION = "impersonation", "Impersonation"
    OTHER = "other", "Other"


class ReportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    REVIEWED = "reviewed", "Reviewed"
    ACTIONED = "actioned", "Action taken"
    DISMISSED = "dismissed", "Dismissed"


class Block(models.Model):
    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="blocked_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="unique_block_edge"),
            models.CheckConstraint(condition=~models.Q(blocker=models.F("blocked")), name="no_self_block"),
        ]

    def __str__(self):
        return f"{self.blocker_id} blocked {self.blocked_id}"


class UserReport(models.Model):
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_reports_made"
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_reports_received"
    )
    reason = models.CharField(max_length=20, choices=ReportReason.choices, default=ReportReason.OTHER)
    details = models.TextField(blank=True, max_length=1000)
    status = models.CharField(
        max_length=20, choices=ReportStatus.choices, default=ReportStatus.PENDING, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["reporter", "reported_user"], name="unique_user_report"),
            models.CheckConstraint(condition=~models.Q(reporter=models.F("reported_user")), name="no_self_report"),
        ]

    def __str__(self):
        return f"Report on user {self.reported_user_id} by {self.reporter_id}"
