import { useNavigate } from "react-router-dom";
import ConversationSidebar from "../conversations/ConversationSidebar";

interface Props {
  workflowId: string;
  deploymentId: string;
}

/**
 * Workflow deployment Conversations tab (POC-5). Mirror of the agent
 * ConversationsTab, but scoped to a WORKFLOW: a workflow's transcript is authored
 * by its members, so the list is resolved server-side through the workflow's
 * parent runs (GET /workflows/{id}/conversations) rather than by an agent_name.
 * Selecting a row (or New) navigates to the workflow chat route
 * (`/workflows/:id/d/:depId/chat`), reusing WorkflowChatPage's `?session` seed.
 */
export default function WorkflowConversationsTab({ workflowId, deploymentId }: Props) {
  const navigate = useNavigate();
  return (
    <div className="p-6 max-w-xl">
      <ConversationSidebar
        scope={{ kind: "workflow", workflowId, deploymentId }}
        activeThreadId={null}
        onSelect={(s) =>
          navigate(`/workflows/${workflowId}/d/${deploymentId}/chat?session=${s.thread_id}`)
        }
        onNew={() => navigate(`/workflows/${workflowId}/d/${deploymentId}/chat`)}
      />
    </div>
  );
}
