from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.contrib.auth.models import User

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)

        # Add extra fields to response
        data['id']       = self.user.pk        # type: ignore # ← use pk instead of id
        data['username'] = self.user.username # pyright: ignore[reportOptionalMemberAccess]
        data['name']     = self.user.get_full_name() or self.user.username # type: ignore
        data['email']    = self.user.email # type: ignore

        return data

class RegisterSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('username', 'email', 'password')
        extra_kwargs = {'password': {'write_only': True}}

    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password']
        )
        return user