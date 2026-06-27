import { useEffect } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getWorkflow } from '../api/registryApi';
import { useWorkflowStore } from '../stores/workflowStore';
import { deserializeWorkflow, type WorkflowDefinition } from '../utils/workflowSerializer';
import Canvas from '../components/Canvas';

export default function CanvasPage() {
  const { id } = useParams<{ id?: string }>();
  const store = useWorkflowStore();

  const { data: workflow, isLoading } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => getWorkflow(id!),
    enabled: !!id,
  });

  useEffect(() => {
    if (!id) {
      store.resetCanvas();
      return;
    }
    if (workflow) {
      const { nodes, edges } = deserializeWorkflow(
        workflow.definition as WorkflowDefinition
      );
      store.setNodes(nodes);
      store.setEdges(edges);
      store.markSaved(workflow.id, workflow.name, workflow.team);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow, id]);

  if (id && isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400">
        Loading workflow…
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-3.5rem)]">
      <Canvas />
    </div>
  );
}
