from rest_framework import viewsets
from accounts.serializers import UserSerializer
from accounts.models import User
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from rest_framework import status, generics
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView
from accounts.serializers import UserSerializer, RegisterSerializer
from accounts.models import User, UserRole
import uuid
from rest_framework.decorators import api_view
import logging
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated


logger = logging.getLogger(__name__)


class LoginView(APIView):
    """
    Authenticate user and return JWT access + refresh tokens
    """

    permission_classes = [AllowAny]

    def post(self, request):
        email_input = request.data.get('email', '').strip()
        password = request.data.get('password', '')

        if not email_input or not password:
            return Response({"detail": "Email and password are required."}, status=400)

        # Find user case-insensitively
        try:
            user = User.objects.get(email__iexact=email_input)
        except User.DoesNotExist:
            logger.warning(f"Login failed - email not found: {email_input}")
            return Response({"detail": "Invalid credentials."}, status=401)

        # Now authenticate with the **normalized** email from DB
        authenticated_user = authenticate(email=user.email, password=password)

        if authenticated_user is None:
            logger.warning(f"Password mismatch for: {user.email}")
            return Response({"detail": "Invalid credentials."}, status=401)

        if not authenticated_user.is_active:
            return Response({"detail": "Account is inactive."}, status=403)

        refresh = RefreshToken.for_user(authenticated_user)
        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": UserSerializer(authenticated_user, context={'request': request}).data
        })


class LogoutView(APIView):
    """
    Blacklist refresh token to logout
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh")
            if not refresh_token:
                return Response({"detail": "Refresh token is required."}, status=400)

            token = RefreshToken(refresh_token)
            token.blacklist()

            return Response({"detail": "Successfully logged out."}, status=205)
        except TokenError:
            return Response({"detail": "Invalid or expired refresh token."}, status=400)
        except Exception as e:
            logger.error(f"Logout error: {str(e)}")
            return Response({"detail": "Logout failed."}, status=500)


class RegisterView(APIView):
    """
    Register a new user account.
    - Creates inactive user
    - Sends verification email
    - Returns user data (not tokens — user must verify first)
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data

        try:
            user = User.objects.create_user(
                email=validated_data["email"],
                full_name=validated_data["full_name"],
                password=validated_data["password"],
                phone_number=validated_data.get("phone_number"),
                role=validated_data.get("role", UserRole.SHOP_ADMIN),
            )

            # Generate and save verification token
            verification_token = str(uuid.uuid4())
            user.verification_token = verification_token
            user.is_active = False
            user.save(update_fields=["verification_token", "is_active"])

            # Send verification email
            verification_url = (
                f"{settings.FRONTEND_URL.rstrip('/')}/verify-email/{verification_token}"
            )
            try:
                send_mail(
                    subject="Activate Your SHOP Manager Account",
                    message=f"Please activate your account by clicking this link:\n\n{verification_url}\n\nThis link expires in 48 hours.",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                logger.info(f"Verification email sent to {user.email}")
            except Exception as e:
                logger.error(
                    f"Failed to send verification email to {user.email}: {str(e)}"
                )
                # We still return success — don't fail registration because of email

            return Response(
                {
                    "message": "Registration successful. Please check your email to verify your account.",
                    "user": UserSerializer(user, context={"request": request}).data,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception("Registration error")
            return Response(
                {
                    "detail": "An error occurred during registration.",
                    "error_type": "server_error",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class VerifyEmailView(APIView):
    """
    Verify email address using token sent in email
    """

    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            user = User.objects.get(verification_token=token)
        except User.DoesNotExist:
            return Response(
                {"detail": "Invalid or expired verification token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.is_active:
            return Response(
                {"detail": "Account is already active."}, status=status.HTTP_200_OK
            )

        user.is_active = True
        user.verification_token = None  # Clear token
        user.save(update_fields=["is_active", "verification_token"])

        return Response(
            {"message": "Email verified successfully. You can now log in."},
            status=status.HTTP_200_OK,
        )


# ==================== User ViewSet ====================


class UserViewSet(viewsets.ModelViewSet):
    """
    A viewset for viewing and editing user instances.
    """

    queryset = User.objects.all()
    serializer_class = UserSerializer
    # permission_classes = [IsAuthenticated]


@api_view(["POST"])
def check_email(request):
    email = request.data.get("email")
    if not email:
        return Response({"error": "Email is required"}, status=400)

    user = User.objects.filter(email=email).first()
    if not user:
        return Response({"exists": False, "is_active": False}, status=200)

    return Response({"exists": True, "is_active": user.is_active}, status=200)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Regular users can only see themselves
        # You can later extend this for shop admins to see their shop users
        if self.request.user.is_staff:
            return User.objects.all()
        return User.objects.filter(id=self.request.user.id)

    def perform_update(self, serializer):
        # Prevent changing role / is_staff unless superuser
        if not self.request.user.is_staff:
            serializer.initial_data.pop("role", None)
            serializer.initial_data.pop("is_staff", None)
        serializer.save()


@api_view(["POST"])
def resend_confirmation_email(request):
    """Resend confirmation email for inactive accounts."""
    email = request.data.get("email")

    if not email:
        return Response(
            {"detail": "Email address is required."}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.get(email=email.lower())

        if user.is_active:
            return Response(
                {
                    "detail": "This account is already active. You can sign in normally.",
                    "error_type": "already_active",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate new verification token
        verification_token = str(uuid.uuid4())
        user.verification_token = verification_token
        user.save()

        # Send confirmation email
        try:
            verification_url = (
                f"{settings.FRONTEND_URL}/verify-email/{verification_token}/"
            )
            send_mail(
                subject="Verify Your SHOP Manager Account",
                message=f"Please verify your email by clicking this link: {verification_url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )

            return Response(
                {
                    "message": "Confirmation email sent successfully. Please check your inbox.",
                    "email_sent": True,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Failed to send confirmation email to {email}: {str(e)}")
            return Response(
                {
                    "detail": "Failed to send confirmation email. Please try again later.",
                    "error_type": "email_send_failed",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    except User.DoesNotExist:
        return Response(
            {
                "detail": "No account found with this email address.",
                "error_type": "user_not_found",
            },
            status=status.HTTP_404_NOT_FOUND,
        )
