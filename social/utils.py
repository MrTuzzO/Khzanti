from .models import Block


def blocked_user_ids(user):
    """IDs of users the viewer blocked or who blocked the viewer (hidden both ways)."""
    ids = set(Block.objects.filter(blocker=user).values_list("blocked_id", flat=True))
    ids.update(Block.objects.filter(blocked=user).values_list("blocker_id", flat=True))
    return ids
