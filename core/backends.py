from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailModelBackend(ModelBackend):
    """
    Authentication backend that authenticates users strictly by email address (case-insensitive).
    Username authentication is not permitted.
    """

    def authenticate(self, request, username=None, password=None, email=None, **kwargs):
        UserModel = get_user_model()
        email_val = email or username or kwargs.get("email") or kwargs.get(UserModel.USERNAME_FIELD)

        if not email_val or not password:
            return None

        email_clean = str(email_val).strip()
        if not email_clean:
            return None

        # Authenticate strictly by email address only
        users = UserModel.objects.filter(email__iexact=email_clean).exclude(email="")

        for user in users:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user

        return None
