import { useEffect, useMemo } from "react";
import { Page, PresenceField, PresenceSignal } from "@ds";

function Field({ thinking, offline }: { thinking: boolean; offline: boolean }) {
  const signal = useMemo(() => new PresenceSignal(), []);
  useEffect(() => signal.setThinking(thinking), [signal, thinking]);
  return (
    <div style={{ position: "relative", height: 190 }}>
      <PresenceField signal={signal} offline={offline} />
    </div>
  );
}

export const AtRest = () => (<Page padding={0}><Field thinking={false} offline={false} /></Page>);
export const Thinking = () => (<Page theme="light" padding={0}><Field thinking offline={false} /></Page>);
export const Offline = () => (<Page padding={0}><Field thinking={false} offline /></Page>);
