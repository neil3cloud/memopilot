import type { NavigationItemDTO } from './types';

/**
 * All MemoPilot navigation items. Controls what appears in the panel sidebar.
 * Items marked enabled: false show as "Coming soon" placeholders.
 */
export const NAVIGATION_ITEMS: NavigationItemDTO[] = [
    {
        id: 'workspace-status',
        label: 'Workspace Status',
        icon: '$(pulse)',
        enabled: true,
        description: 'Backend connection, indexing progress, workspace overview.',
    },
    {
        id: 'local-memory',
        label: 'Local App Memory',
        icon: '$(database)',
        enabled: true,
        description: 'Browse indexed symbols, file summaries, and learned patterns.',
    },
    {
        id: 'rules-skills',
        label: 'Rules & Skills',
        icon: '$(law)',
        enabled: true,
        description: 'Active rules (global + project) and detected skills/frameworks.',
    },
    {
        id: 'task-entry',
        label: 'New Task',
        icon: '$(edit)',
        enabled: true,
        description: 'Enter a natural language task with constraints and mode selection.',
    },
    {
        id: 'context-pack',
        label: 'Context Pack',
        icon: '$(package)',
        enabled: true,
        description: 'Preview files, rules, and tokens that will be sent to the AI model.',
    },
    {
        id: 'model-routing',
        label: 'Model Routing',
        icon: '$(server)',
        enabled: true,
        description: 'See which model was selected, why, and available alternatives.',
    },
    {
        id: 'patch-preview',
        label: 'Diff Preview',
        icon: '$(diff)',
        enabled: true,
        description: 'Review AI-generated code changes as a unified diff.',
    },
    {
        id: 'approval-gate',
        label: 'Approval Gate',
        icon: '$(check)',
        enabled: true,
        description: 'Approve or reject patches before they are written to disk.',
    },
    {
        id: 'validation',
        label: 'Validation',
        icon: '$(beaker)',
        enabled: true,
        description: 'Test, lint, and type-check results after patch application.',
    },
    {
        id: 'task-history',
        label: 'Tasks & History',
        icon: '$(history)',
        enabled: true,
        description: 'Browse past tasks with cost, duration, and files changed.',
    },
    {
        id: 'cost-dashboard',
        label: 'Cost Dashboard',
        icon: '$(graph)',
        enabled: true,
        description: 'Cost trends, model efficiency, and savings over time.',
    },
    {
        id: 'memory-manager',
        label: 'Memory Manager',
        icon: '$(archive)',
        enabled: true,
        description: 'Manage memory items: approve, reject, edit, filter, backup.',
    },
    {
        id: 'workspace-profile',
        label: 'Workspace Profile',
        icon: '$(file-code)',
        enabled: true,
        description: 'View and validate the workspace profile configuration.',
    },
    {
        id: 'privacy-boundary',
        label: 'Privacy Dashboard',
        icon: '$(shield)',
        enabled: true,
        description: 'Data classification: what stays local vs. what may leave.',
    },
    {
        id: 'provider-matrix',
        label: 'Provider Matrix',
        icon: '$(table)',
        enabled: true,
        description: 'AI provider capabilities, costs, and privacy levels.',
    },
    {
        id: 'evidence-board',
        label: 'Evidence Board',
        icon: '$(search)',
        enabled: true,
        description: 'Attached evidence sources with trust classification.',
    },
    {
        id: 'mcp-tools',
        label: 'MCP & Tools',
        icon: '$(plug)',
        enabled: true,
        description: 'Connected MCP servers, available tools, and data flow.',
    },
];
