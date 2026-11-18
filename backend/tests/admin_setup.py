"""Explicit administrator provisioning for tests of protected functionality."""


def register_admin(service, **kwargs):
    user = service.register_user(**kwargs)
    service.set_user_admin_role(user_id=user.user_id, is_admin=True)
    return service.get_user_by_token(user.token)
