def get_profile_constraints(user):
    """
    Safely reads optional profile constraints from a User instance.
    Returns None for any field that is missing or unauthenticated.
    """
    if not user or not getattr(user, "is_authenticated", True):
        return {
            "gender": None,
            "height": None,
            "age": None,
            "body_type": None,
        }

    return {
        "gender": getattr(user, "gender", None),
        "height": getattr(user, "height", None),
        "age": getattr(user, "age", None),
        "body_type": getattr(user, "body_type", None),
    }
