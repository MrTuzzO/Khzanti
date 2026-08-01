from django.utils import timezone

from .models import User


def _pct_change(current, previous):
    if previous == 0:
        return None if current == 0 else 100
    return round((current - previous) / previous * 100)


def dashboard_callback(request, context):
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)

    total_users = User.objects.count()
    total_before_this_month = User.objects.filter(created_at__lt=month_start).count()

    new_this_month = User.objects.filter(created_at__gte=month_start).count()
    new_last_month = User.objects.filter(created_at__gte=prev_month_start, created_at__lt=month_start).count()

    verified_users = User.objects.filter(is_email_verified=True).count()
    verified_pct = round(verified_users / total_users * 100) if total_users else 0

    context.update(
        {
            "stats": [
                {
                    "title": "Total Users",
                    "value": total_users,
                    "change": _pct_change(total_users, total_before_this_month),
                    "caption": "vs last month",
                },
                {
                    "title": "New Users",
                    "value": new_this_month,
                    "change": _pct_change(new_this_month, new_last_month),
                    "caption": "vs last month",
                },
                {
                    "title": "Verified Users",
                    "value": verified_users,
                    "change": None,
                    "caption": f"{verified_pct}% of total users",
                },
            ],
            "recent_users": User.objects.order_by("-created_at")[:8],
        }
    )
    return context
