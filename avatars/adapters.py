def get_profile_constraints(user):
    """
    Safely reads optional profile constraints from a User instance or user profile.
    Returns None for any field that is missing or unauthenticated.
    """
    if not user or not getattr(user, "is_authenticated", True):
        return {
            "gender": None,
            "height": None,
            "age": None,
            "body_type": None,
            "weight": None,
            "build": None,
        }

    profile = getattr(user, "profile", None) or user

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
    }

