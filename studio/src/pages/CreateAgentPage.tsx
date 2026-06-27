import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";
import { createAgent, listProviders, listTeams } from "../api/registryApi";
import { cn } from "../lib/utils";

const schema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(128, "Name too long")
    .regex(/^[a-z0-9-]+$/, "Lowercase letters, numbers, and hyphens only"),
  team: z.string().min(1, "Team is required"),
  description: z.string().max(512).optional(),
  agent_type: z.enum(["sdk", "declarative"]),
  instructions: z.string().optional(),
  llm_provider_id: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

export default function CreateAgentPage() {
  const navigate = useNavigate();

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting, dirtyFields },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      team: "",
      description: "",
      agent_type: "sdk",
      instructions: "",
    },
  });

  const selectedTeam = watch("team");
  const selectedProviderId = watch("llm_provider_id");

  const { data: teamsData } = useQuery({
    queryKey: ["teams"],
    queryFn: listTeams,
  });

  const { data: providersData } = useQuery({
    queryKey: ["providers", selectedTeam],
    queryFn: () => listProviders(selectedTeam || undefined),
    enabled: !!selectedTeam,
  });

  const selectedProvider = providersData?.items.find((p) => p.id === selectedProviderId);

  const mutation = useMutation({
    mutationFn: (values: FormValues) =>
      createAgent({
        name: values.name,
        team: values.team,
        description: values.description || undefined,
        agent_type: values.agent_type,
        llm_provider_id: values.llm_provider_id || undefined,
      }),
    onSuccess: (agent) => {
      toast.success(`Agent "${agent.name}" registered.`);
      setTimeout(() => navigate("/"), 800);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof Error ? err.message : "Failed to create agent.");
    },
  });

  const onSubmit = (values: FormValues) => mutation.mutate(values);

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <button
        onClick={() => navigate("/")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900 mb-6 transition-colors"
      >
        <ArrowLeft size={14} />
        Back to agents
      </button>

      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900">Register New Agent</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          Add an agent to the registry so it can be versioned and deployed.
        </p>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="card space-y-5" noValidate>
        {/* Name */}
        <Field label="Agent name" required error={errors.name?.message}>
          <input
            {...register("name")}
            className={cn("input", errors.name && "border-red-400 focus:border-red-500 focus:ring-red-500")}
            placeholder="my-agent"
          />
          <FieldHint>Lowercase letters, numbers, hyphens. Used as the Kubernetes workload name.</FieldHint>
        </Field>

        {/* Team */}
        <Field label="Team" required error={errors.team?.message}>
          <select
            {...register("team")}
            className={cn("input", errors.team && "border-red-400")}
          >
            <option value="">— select team —</option>
            {teamsData?.items.map((t) => (
              <option key={t.id} value={t.name}>{t.name}</option>
            ))}
          </select>
        </Field>

        {/* LLM Provider */}
        <Field label="LLM Provider" error={errors.llm_provider_id?.message}>
          <select {...register("llm_provider_id")} className="input" disabled={!selectedTeam}>
            <option value="">— none (agent manages its own credentials) —</option>
            {providersData?.items.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.provider})
              </option>
            ))}
          </select>
          {selectedTeam && providersData?.items.length === 0 && (
            <p className="text-xs text-amber-600 mt-0.5">
              No providers for this team.{" "}
              <a href="/providers" className="underline hover:text-amber-800">
                Add one in Providers →
              </a>
            </p>
          )}
          {!selectedTeam && (
            <p className="text-xs text-slate-400 mt-0.5">Select a team first to see available providers.</p>
          )}
        </Field>

        {/* Model — derived from provider or unmanaged */}
        <div className="flex items-center gap-2 px-3 py-2.5 rounded-md bg-slate-50 border border-slate-200 text-sm">
          <span className="text-slate-500 shrink-0">Model</span>
          {selectedProvider ? (
            <span className="font-mono text-slate-800 text-xs">{selectedProvider.default_model}</span>
          ) : (
            <span className="text-slate-400 italic">determined by the agent's own code</span>
          )}
          {selectedProvider && (
            <span className="ml-auto text-xs text-slate-400">from provider · read-only</span>
          )}
        </div>

        {/* Description */}
        <Field label="Description" error={errors.description?.message}>
          <textarea
            {...register("description")}
            className="input resize-none"
            rows={2}
            placeholder="What does this agent do?"
          />
        </Field>

        {/* Agent type */}
        <Field label="Agent type" error={errors.agent_type?.message}>
          <select {...register("agent_type")} className="input">
            <option value="sdk">SDK</option>
            <option value="declarative">Declarative</option>
          </select>
        </Field>

        {/* Instructions */}
        <Field label="Instructions" error={errors.instructions?.message}>
          <textarea
            {...register("instructions")}
            className="input resize-none"
            rows={4}
            placeholder="System prompt / instructions for the agent…"
          />
        </Field>

        {/* Actions */}
        <div className="flex items-center justify-end gap-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={() => navigate("/")} className="btn-secondary">
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting || mutation.isPending}
            className="btn-primary"
          >
            {(isSubmitting || mutation.isPending) ? (
              <><Loader2 size={14} className="animate-spin" /> Registering…</>
            ) : (
              "Register Agent"
            )}
          </button>
        </div>

        {Object.keys(errors).length > 0 && Object.keys(dirtyFields).length > 0 && (
          <p className="text-xs text-red-500">
            Please fix the errors above before submitting.
          </p>
        )}
      </form>
    </div>
  );
}

function Field({
  label,
  required,
  error,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="label">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

function FieldHint({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-slate-400 mt-0.5">{children}</p>;
}
