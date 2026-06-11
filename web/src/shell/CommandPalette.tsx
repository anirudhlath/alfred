import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Command, CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList,
} from "@/components/ui/command";
import { post } from "@/lib/api";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const run = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) => post(path, body),
    onSuccess: (_d, v) => {
      toast(`Done: ${v.path}`);
      void qc.invalidateQueries({ queryKey: ["overview"] });
      void qc.invalidateQueries({ queryKey: ["deferred"] });
      void qc.invalidateQueries({ queryKey: ["triggers"] });
    },
    onError: (e) => toast.error(String(e)),
  });

  const go = (to: string) => { navigate(to); setOpen(false); };
  const act = (path: string, body?: unknown) => { run.mutate({ path, body }); setOpen(false); };

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <Command>
        <CommandInput placeholder="Command or destination…" className="font-mono" />
        <CommandList className="font-mono text-xs">
          <CommandEmpty>Nothing matches.</CommandEmpty>
          <CommandGroup heading="Go to">
            <CommandItem onSelect={() => go("/")}>Chat</CommandItem>
            <CommandItem onSelect={() => go("/activity")}>Activity</CommandItem>
            <CommandItem onSelect={() => go("/memory")}>Memory</CommandItem>
            <CommandItem onSelect={() => go("/triggers")}>Triggers</CommandItem>
            <CommandItem onSelect={() => go("/health")}>Health</CommandItem>
            <CommandItem onSelect={() => go("/settings")}>Settings</CommandItem>
          </CommandGroup>
          <CommandGroup heading="Controls">
            <CommandItem onSelect={() => act("/api/admin/dnd", { active: true })}>DND on</CommandItem>
            <CommandItem onSelect={() => act("/api/admin/dnd", { active: false })}>DND off</CommandItem>
            <CommandItem onSelect={() => act("/api/admin/notifications/drain")}>Drain deferred notifications</CommandItem>
            <CommandItem onSelect={() => act("/api/admin/librarian/run")}>Run Librarian now</CommandItem>
          </CommandGroup>
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
