from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameModelBackend(ModelBackend):
    """
    Custom authentication backend allowing users to sign in
    using either their registered email address or their username,
    both evaluated case-insensitively.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD) or kwargs.get("email")

        if not username or not password:
            return None

        identifier = str(username).strip()
        if not identifier:
            return None

        # Case-insensitive match on username OR non-empty email
        query = Q(username__iexact=identifier)
        query |= (Q(email__iexact=identifier) & ~Q(email=""))

        users = UserModel.objects.filter(query)

        for user in users:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user

        return None
