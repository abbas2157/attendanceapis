from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        # Add extra fields to response
        data['id']       = self.user.pk        # type: ignore # ← use pk instead of id
        data['username'] = self.user.username # pyright: ignore[reportOptionalMemberAccess]
        data['name']     = self.user.get_full_name() or self.user.username # type: ignore
        data['email']    = self.user.email # type: ignore

        return data
    