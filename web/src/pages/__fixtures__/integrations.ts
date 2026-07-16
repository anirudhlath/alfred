import type { IntegrationInfo } from "@/lib/types";

/**
 * Shared fixture: a registry-declared sovereign service (kind="service"),
 * mirroring what GET /api/integrations returns for home-service.
 * Used by IntegrationCard.test.tsx and SettingsPage.test.tsx.
 */
export const HOME_SERVICE_INTEGRATION: IntegrationInfo = {
  name: "home-service",
  category: "service",
  kind: "service",
  schema: {
    fields: {
      url: {
        label: "Home Assistant URL",
        field_type: "url",
        required: true,
        placeholder: "",
        default: "http://homeassistant.local:8123",
        help_text: "",
        transient: false,
      },
      token: {
        label: "Access Token",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "Long-lived access token from your HA profile page",
        transient: false,
      },
    },
  },
  configured: { url: false, token: false },
};
