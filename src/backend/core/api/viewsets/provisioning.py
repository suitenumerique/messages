"""API view for provisioning mail domains from DeployCenter."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import HasProvisioningApiKey
from core.api.serializers import ProvisioningMailDomainSerializer
from core.models import MailDomain


class ProvisioningMailDomainView(APIView):
    """Provision mail domains from DeployCenter webhooks."""

    permission_classes = [HasProvisioningApiKey]
    authentication_classes = []

    def post(self, request):
        """Provision mail domains from a list of domain names."""
        serializer = ProvisioningMailDomainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        domains = serializer.validated_data["domains"]
        custom_attributes = serializer.validated_data.get("custom_attributes", {})

        created = []
        existing = []
        errors = []

        for domain_name in domains:
            try:
                domain, was_created = MailDomain.objects.get_or_create(
                    name=domain_name,
                    defaults={"custom_attributes": custom_attributes},
                )
                if was_created:
                    created.append(domain_name)
                else:
                    if domain.custom_attributes != custom_attributes:
                        domain.custom_attributes = custom_attributes
                        domain.save()
                    existing.append(domain_name)
            except (ValidationError, IntegrityError) as e:
                errors.append({"domain": domain_name, "error": str(e)})

        return Response(
            {
                "created": created,
                "existing": existing,
                "errors": errors,
            },
            status=status.HTTP_200_OK,
        )
