import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  BrainCircuit,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import {
  createProvider,
  deleteProvider,
  listProviders,
  listTeams,
  type LLMProvider,
} from "../api/registryApi";
import { cn } from "../lib/utils";

// ---------------------------------------------------------------------------
// Model options per provider
// ---------------------------------------------------------------------------
const MODELS: Record<string, string[]> = {
  anthropic: ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"],
  bedrock: [
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
  ],
};

const AWS_REGIONS = [
  "us-east-1", "us-east-2", "us-west-1", "us-west-2",
  "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-northeast-1",
];

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------
const baseSchema = z.object({
  name: z.string().min(1, "Name is required").max(128),
  provider: z.enum(["anthropic", "bedrock"]),
  default_model: z.string().min(1, "Model is required"),
  team: z.string().min(1, "Team is required"),
});

const anthropicSchema = baseSchema.extend({
  provider: z.literal("anthropic"),
  ANTHROPIC_API_KEY: z.string().min(1, "API key is required"),
});

const bedrockSchema = baseSchema.extend({
  provider: z.literal("bedrock"),
  AWS_ACCESS_KEY_ID: z.string().min(1, "Access Key ID is required"),
  AWS_SECRET_ACCESS_KEY: z.string().min(1, "Secret Access Key is required"),
  AWS_DEFAULT_REGION: z.string().min(1, "Region is required"),
});

const schema = z.discriminatedUnion("provider", [anthropicSchema, bedrockSchema]);
type FormValues = z.infer<typeof schema>;

// ---------------------------------------------------------------------------
// Provider badge colours
// ---------------------------------------------------------------------------
const PROVIDER_BADGE: Record<string, string> = {
  anthropic: "bg-purple-100 text-purple-700",
  bedrock: "bg-orange-100 text-orange-700",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ProvidersPage() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);

  const { data: providersData, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["providers"],
    queryFn: () => listProviders(),
    refetchInterval: 30_000,
  });

  const { data: teamsData } = useQuery({
    queryKey: ["teams"],
    queryFn: listTeams,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteProvider(id),
    onSuccess: () => {
      toast.success("Provider deleted.");
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to delete provider.");
    },
  });

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">LLM Providers</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Configure LLM credentials — stored encrypted, injected into agent pods at deploy time
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
          <button onClick={() => setShowForm(true)} className="btn-primary">
            <Plus size={14} />
            Add Provider
          </button>
        </div>
      </div>

      {/* Add form panel */}
      {showForm && (
        <AddProviderForm
          teams={teamsData?.items.map((t) => t.name) ?? []}
          onClose={() => setShowForm(false)}
          onCreated={() => {
            setShowForm(false);
            qc.invalidateQueries({ queryKey: ["providers"] });
          }}
        />
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading providers…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load providers: {String(error)}
        </div>
      )}

      {providersData && (
        providersData.items.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <BrainCircuit size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No providers configured</p>
            <p className="text-slate-400 text-sm mt-1">
              Add a provider so agents can make LLM calls.
            </p>
            <button onClick={() => setShowForm(true)} className="btn-primary mt-5">
              <Plus size={14} />
              Add Provider
            </button>
          </div>
        ) : (
          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Name", "Provider", "Default Model", "Team", ""].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {providersData.items.map((p) => (
                  <ProviderRow
                    key={p.id}
                    provider={p}
                    onDelete={() => {
                      if (confirm(`Delete provider "${p.name}"?`)) {
                        deleteMutation.mutate(p.id);
                      }
                    }}
                    deleting={deleteMutation.isPending && deleteMutation.variables === p.id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------
function ProviderRow({
  provider,
  onDelete,
  deleting,
}: {
  provider: LLMProvider;
  onDelete: () => void;
  deleting: boolean;
}) {
  return (
    <tr className="hover:bg-slate-50 transition-colors">
      <td className="px-4 py-3 font-medium text-slate-900">{provider.name}</td>
      <td className="px-4 py-3">
        <span className={cn("badge", PROVIDER_BADGE[provider.provider] ?? "bg-slate-100 text-slate-600")}>
          {provider.provider}
        </span>
      </td>
      <td className="px-4 py-3 text-slate-600 font-mono text-xs">{provider.default_model}</td>
      <td className="px-4 py-3 text-slate-600">{provider.team}</td>
      <td className="px-4 py-3 text-right">
        <button
          onClick={onDelete}
          disabled={deleting}
          className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
        >
          {deleting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          Delete
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Add form
// ---------------------------------------------------------------------------
function AddProviderForm({
  teams,
  onClose,
  onCreated,
}: {
  teams: string[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { provider: "anthropic", default_model: "claude-sonnet-4-6" },
  });

  const provider = watch("provider");
  const models = MODELS[provider] ?? [];

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      let credentials: Record<string, string>;
      if (values.provider === "anthropic") {
        credentials = { api_key: values.ANTHROPIC_API_KEY };
      } else {
        credentials = {
          aws_access_key_id: values.AWS_ACCESS_KEY_ID,
          aws_secret_access_key: values.AWS_SECRET_ACCESS_KEY,
          aws_region: values.AWS_DEFAULT_REGION,
        };
      }
      return createProvider({
        name: values.name,
        provider: values.provider,
        default_model: values.default_model,
        team: values.team,
        credentials,
      });
    },
    onSuccess: () => {
      toast.success("Provider added.");
      onCreated();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Failed to create provider.");
    },
  });

  return (
    <div className="card mb-6 relative">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"
      >
        <X size={16} />
      </button>
      <h2 className="text-lg font-semibold text-slate-900 mb-5">Add LLM Provider</h2>

      <form onSubmit={handleSubmit((v) => mutation.mutate(v))} className="space-y-4" noValidate>
        <div className="grid grid-cols-2 gap-4">
          {/* Provider type */}
          <Field label="Provider" required error={errors.provider?.message}>
            <select {...register("provider")} className="input">
              <option value="anthropic">Anthropic</option>
              <option value="bedrock">Amazon Bedrock</option>
            </select>
          </Field>

          {/* Name */}
          <Field label="Name" required error={errors.name?.message}>
            <input {...register("name")} className={cn("input", errors.name && "border-red-400")} placeholder="anthropic-prod" />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-4">
          {/* Team */}
          <Field label="Team" required error={(errors as Record<string, {message?: string}>).team?.message}>
            <select {...register("team")} className="input">
              <option value="">— select team —</option>
              {teams.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </Field>

          {/* Default model */}
          <Field label="Default model" required error={(errors as Record<string, {message?: string}>).default_model?.message}>
            <select {...register("default_model")} className="input">
              {models.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>
        </div>

        {/* Dynamic credential fields */}
        {provider === "anthropic" && (
          <Field label="API Key" required error={(errors as Record<string, {message?: string}>).ANTHROPIC_API_KEY?.message}>
            <input
              {...register("ANTHROPIC_API_KEY" as keyof FormValues)}
              type="password"
              autoComplete="off"
              className={cn("input font-mono", (errors as Record<string, {message?: string}>).ANTHROPIC_API_KEY && "border-red-400")}
              placeholder="sk-ant-••••••••"
            />
            <p className="text-xs text-slate-400 mt-0.5">
              Stored AES-256 encrypted. Never returned by the API.
            </p>
          </Field>
        )}

        {provider === "bedrock" && (
          <>
            <div className="grid grid-cols-2 gap-4">
              <Field label="AWS Access Key ID" required error={(errors as Record<string, {message?: string}>).AWS_ACCESS_KEY_ID?.message}>
                <input
                  {...register("AWS_ACCESS_KEY_ID" as keyof FormValues)}
                  type="password"
                  autoComplete="off"
                  className={cn("input font-mono", (errors as Record<string, {message?: string}>).AWS_ACCESS_KEY_ID && "border-red-400")}
                  placeholder="AKIA••••••••••••"
                />
              </Field>
              <Field label="AWS Region" required error={(errors as Record<string, {message?: string}>).AWS_DEFAULT_REGION?.message}>
                <select {...register("AWS_DEFAULT_REGION" as keyof FormValues)} className="input">
                  <option value="">— select region —</option>
                  {AWS_REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </Field>
            </div>
            <Field label="AWS Secret Access Key" required error={(errors as Record<string, {message?: string}>).AWS_SECRET_ACCESS_KEY?.message}>
              <input
                {...register("AWS_SECRET_ACCESS_KEY" as keyof FormValues)}
                type="password"
                autoComplete="off"
                className={cn("input font-mono", (errors as Record<string, {message?: string}>).AWS_SECRET_ACCESS_KEY && "border-red-400")}
                placeholder="••••••••••••••••••••••••••••••••••••••••"
              />
              <p className="text-xs text-slate-400 mt-0.5">
                Stored AES-256 encrypted. Never returned by the API.
              </p>
            </Field>
          </>
        )}

        <div className="flex justify-end gap-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={onClose} className="btn-secondary">
            Cancel
          </button>
          <button type="submit" disabled={isSubmitting || mutation.isPending} className="btn-primary">
            {(isSubmitting || mutation.isPending) ? (
              <><Loader2 size={14} className="animate-spin" /> Saving…</>
            ) : (
              "Save Provider"
            )}
          </button>
        </div>
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
