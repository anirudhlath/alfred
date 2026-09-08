import type { AttentionDomain, IntegrationInfo } from "@/lib/types";

/**
 * `GET /api/integrations`. Two entries on purpose: the setup gate must find
 * `home-service` by name rather than by position.
 */
export const integrationsFixture: IntegrationInfo[] = [
  {
    name: "weather",
    category: "weather",
    kind: "adapter",
    schema: {
      fields: {
        api_key: {
          label: "API key",
          field_type: "password",
          required: true,
          placeholder: "",
          default: "",
          help_text: "",
          transient: false,
        },
      },
    },
    configured: { api_key: false },
  },
  {
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
          default: "http://192.168.1.10:8123",
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
  },
];

/**
 * `GET /api/admin/attention`. Three domains the reflex may be trusted with, and
 * the three it may never be: the setup gate must filter those out.
 */
export const attentionFixture: { domains: AttentionDomain[] } = {
  domains: [
    {
      domain: "light",
      members: ["light.hall", "light.kitchen", "light.living_room"],
      seen: [
        "light.hall",
        "light.kitchen",
        "light.living_room",
        "light.study",
        "light.landing",
        "light.porch",
      ],
    },
    {
      domain: "media_player",
      members: ["media_player.tv"],
      seen: ["media_player.tv", "media_player.kitchen"],
    },
    {
      domain: "fan",
      members: [],
      seen: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    },
    { domain: "lock", members: [], seen: ["lock.front_door", "lock.back_door"] },
    { domain: "alarm_control_panel", members: [], seen: ["alarm_control_panel.house"] },
    { domain: "cover", members: [], seen: ["cover.garage"] },
  ],
};
