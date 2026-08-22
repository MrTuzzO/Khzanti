def get_profile_constraints(user):
    """
    Safely reads optional profile constraints from a User instance or customer_profile.
    Returns None/empty for any field that is missing or unauthenticated.
    """
    if not user or not getattr(user, "is_authenticated", True):
        return {
            "gender": None,
            "height": None,
            "age": None,
            "body_type": None,
            "weight": None,
            "build": None,
            "country": None,
            "aesthetics": [],
        }

    profile = getattr(user, "customer_profile", None) or getattr(user, "profile", None) or user

    aesthetics_list = []
    if profile and hasattr(profile, "aesthetics"):
        try:
            aesthetics_list = list(profile.aesthetics.values_list("name", flat=True))
        except Exception:
            try:
                aesthetics_list = [getattr(a, "name", str(a)) for a in profile.aesthetics.all()]
            except Exception:
                pass

    return {
        "gender": getattr(profile, "gender", None) or getattr(user, "gender", None),
        "height": getattr(profile, "height", None) or getattr(user, "height", None),
        "age": getattr(profile, "age", None) or getattr(user, "age", None),
        "body_type": (
            getattr(profile, "body_type", None)
            or getattr(user, "body_type", None)
            or getattr(profile, "build", None)
            or getattr(user, "build", None)
        ),
        "weight": getattr(profile, "weight", None) or getattr(user, "weight", None),
        "country": getattr(profile, "country", None) or getattr(user, "country", None),
        "aesthetics": aesthetics_list,
    }


