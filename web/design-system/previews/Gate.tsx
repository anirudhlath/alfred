import { Page, Gate, StepList } from "@ds";

export const SignIn = () => (
  <Page theme="dark" frame="phone">
    <Gate kicker="alfred.local · signed out" title="Welcome back, sir." primary={{ label: "Sign in with a passkey", onClick: () => {} }} foot="The passkey never leaves this device." />
  </Page>
);
export const WithSteps = () => (
  <Page theme="light" frame="phone">
    <Gate
      kicker="first run · alfred.local"
      title="Good morning. I am Alfred."
      body="A passkey on this phone is the only key to the house's assistant."
      primary={{ label: "Create a passkey", onClick: () => {} }}
      secondary={{ label: "Not now", onClick: () => {} }}
      foot="Nothing here phones home."
    >
      <StepList variant="progress" steps={[
        { label: "Passkey", state: "current" },
        { label: "Home Assistant", state: "todo" },
        { label: "What the reflex may touch", state: "todo" },
      ]} />
    </Gate>
  </Page>
);
export const WithPointer = () => (
  <Page theme="dark" frame="phone">
    <Gate kicker="reaching you · 3 of 6 · in Safari" title="Add me to the home screen." secondary={{ label: "Not now", onClick: () => {} }} pointer="Share" />
  </Page>
);
