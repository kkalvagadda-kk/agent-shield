import { useNavigate } from "react-router-dom";
import ConversationSidebar from "../conversations/ConversationSidebar";

interface Props {
  agentName: string;
  deploymentId: string;
}

/**
 * Deployment Conversations tab (POC-5). A thin wrapper around the shared
 * ConversationSidebar scoped to this deployment's threads. It owns no chat
 * logic — selecting a row (or New) navigates to the deployment chat route
 * (`/agents/:name/d/:depId/chat`), reusing AgentChatPage's `?session` seed so
 * the full chat machinery rehydrates the transcript.
 */
export default function ConversationsTab({ agentName, deploymentId }: Props) {
  const navigate = useNavigate();
  return (
    <div className="p-6 max-w-xl">
      <ConversationSidebar
        scope={{ kind: "agent", agentName, deploymentId }}
        activeThreadId={null}
        onSelect={(s) =>
          navigate(`/agents/${agentName}/d/${deploymentId}/chat?session=${s.thread_id}`)
        }
        onNew={() => navigate(`/agents/${agentName}/d/${deploymentId}/chat`)}
      />
    </div>
  );
}
