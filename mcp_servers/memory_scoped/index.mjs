#!/usr/bin/env node
// ---------------------------------------------------------------------------
// @modelcontextprotocol/server-memory 의 포크 — 대화별 지식 그래프.
//
// 원본: https://github.com/modelcontextprotocol/servers (memory, MIT)
//       node_modules/@modelcontextprotocol/server-memory/dist/index.js v0.6.3
//
// 왜 포크했나
//   원본은 프로세스 하나에 그래프 파일 하나(MEMORY_FILE_PATH)입니다. 서버가
//   여러 대화에 공유되므로 다음 대화가 이전 대화의 기억을 그대로 읽습니다.
//   격리 단위를 프로세스 수명이 아니라 요청이 들고 오는 명시적 스코프로
//   옮깁니다. 샌드박스 MCP 의 namespace 와 같은 패턴입니다.
//
// 바꾼 것 (아래 세 군데. 나머지는 원본과 동일하게 두어 상위 버전을 따라잡기
// 쉽게 했습니다)
//   1. 그래프 라우팅 블록: `<MEMORY_GRAPH_DIR>/<graph_id>.jsonl` 로 분리하고,
//      요청 스코프를 AsyncLocalStorage 로 들고 다닙니다.
//   2. registerTool / registerResource 를 감싸 모든 도구에 graph_id 를 붙이고
//      핸들러를 그 요청의 그래프 컨텍스트 안에서 실행합니다.
//   3. main() 이 단일 그래프 대신 그래프 디렉터리를 준비합니다.
//
// 스코프 우선순위: 요청 _meta > graph_id 인자 > 프로세스 한정 폴백.
// ---------------------------------------------------------------------------
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { SubscribeRequestSchema, UnsubscribeRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { AsyncLocalStorage } from 'node:async_hooks';
// Define memory file path using environment variable with fallback
export const defaultMemoryPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'memory.jsonl');
// Handle backward compatibility: migrate memory.json to memory.jsonl if needed
export async function ensureMemoryFilePath() {
    if (process.env.MEMORY_FILE_PATH) {
        // Custom path provided, use it as-is (with absolute path resolution)
        return path.isAbsolute(process.env.MEMORY_FILE_PATH)
            ? process.env.MEMORY_FILE_PATH
            : path.join(path.dirname(fileURLToPath(import.meta.url)), process.env.MEMORY_FILE_PATH);
    }
    // No custom path set, check for backward compatibility migration
    const oldMemoryPath = path.join(path.dirname(fileURLToPath(import.meta.url)), 'memory.json');
    const newMemoryPath = defaultMemoryPath;
    try {
        // Check if old file exists and new file doesn't
        await fs.access(oldMemoryPath);
        try {
            await fs.access(newMemoryPath);
            // Both files exist, use new one (no migration needed)
            return newMemoryPath;
        }
        catch {
            // Old file exists, new file doesn't - migrate
            console.error('DETECTED: Found legacy memory.json file, migrating to memory.jsonl for JSONL format compatibility');
            await fs.rename(oldMemoryPath, newMemoryPath);
            console.error('COMPLETED: Successfully migrated memory.json to memory.jsonl');
            return newMemoryPath;
        }
    }
    catch {
        // Old file doesn't exist, use new path
        return newMemoryPath;
    }
}
// The KnowledgeGraphManager class contains all operations to interact with the knowledge graph
export class KnowledgeGraphManager {
    memoryFilePath;
    constructor(memoryFilePath) {
        this.memoryFilePath = memoryFilePath;
    }
    async loadGraph() {
        try {
            const data = await fs.readFile(this.memoryFilePath, "utf-8");
            const lines = data.split("\n").filter(line => line.trim() !== "");
            return lines.reduce((graph, line) => {
                const item = JSON.parse(line);
                if (item.type === "entity") {
                    graph.entities.push({
                        name: item.name,
                        entityType: item.entityType,
                        observations: item.observations
                    });
                }
                if (item.type === "relation") {
                    graph.relations.push({
                        from: item.from,
                        to: item.to,
                        relationType: item.relationType
                    });
                }
                return graph;
            }, { entities: [], relations: [] });
        }
        catch (error) {
            if (error instanceof Error && 'code' in error && error.code === "ENOENT") {
                return { entities: [], relations: [] };
            }
            throw error;
        }
    }
    async saveGraph(graph) {
        const lines = [
            ...graph.entities.map(e => JSON.stringify({
                type: "entity",
                name: e.name,
                entityType: e.entityType,
                observations: e.observations
            })),
            ...graph.relations.map(r => JSON.stringify({
                type: "relation",
                from: r.from,
                to: r.to,
                relationType: r.relationType
            })),
        ];
        await fs.writeFile(this.memoryFilePath, lines.join("\n"));
    }
    async createEntities(entities) {
        const graph = await this.loadGraph();
        const newEntities = entities.filter(e => !graph.entities.some(existingEntity => existingEntity.name === e.name));
        graph.entities.push(...newEntities);
        await this.saveGraph(graph);
        return newEntities;
    }
    async createRelations(relations) {
        const graph = await this.loadGraph();
        const newRelations = relations.filter(r => !graph.relations.some(existingRelation => existingRelation.from === r.from &&
            existingRelation.to === r.to &&
            existingRelation.relationType === r.relationType));
        graph.relations.push(...newRelations);
        await this.saveGraph(graph);
        return newRelations;
    }
    async addObservations(observations) {
        const graph = await this.loadGraph();
        const results = observations.map(o => {
            const entity = graph.entities.find(e => e.name === o.entityName);
            if (!entity) {
                throw new Error(`Entity with name ${o.entityName} not found`);
            }
            const newObservations = o.contents.filter(content => !entity.observations.includes(content));
            entity.observations.push(...newObservations);
            return { entityName: o.entityName, addedObservations: newObservations };
        });
        await this.saveGraph(graph);
        return results;
    }
    async deleteEntities(entityNames) {
        const graph = await this.loadGraph();
        graph.entities = graph.entities.filter(e => !entityNames.includes(e.name));
        graph.relations = graph.relations.filter(r => !entityNames.includes(r.from) && !entityNames.includes(r.to));
        await this.saveGraph(graph);
    }
    async deleteObservations(deletions) {
        const graph = await this.loadGraph();
        deletions.forEach(d => {
            const entity = graph.entities.find(e => e.name === d.entityName);
            if (entity) {
                entity.observations = entity.observations.filter(o => !d.observations.includes(o));
            }
        });
        await this.saveGraph(graph);
    }
    async deleteRelations(relations) {
        const graph = await this.loadGraph();
        graph.relations = graph.relations.filter(r => !relations.some(delRelation => r.from === delRelation.from &&
            r.to === delRelation.to &&
            r.relationType === delRelation.relationType));
        await this.saveGraph(graph);
    }
    async readGraph() {
        return this.loadGraph();
    }
    // Very basic search function
    async searchNodes(query) {
        const graph = await this.loadGraph();
        // Filter entities
        const filteredEntities = graph.entities.filter(e => e.name.toLowerCase().includes(query.toLowerCase()) ||
            e.entityType.toLowerCase().includes(query.toLowerCase()) ||
            e.observations.some(o => o.toLowerCase().includes(query.toLowerCase())));
        // Create a Set of filtered entity names for quick lookup
        const filteredEntityNames = new Set(filteredEntities.map(e => e.name));
        // Include relations where at least one endpoint matches the search results.
        // This lets callers discover connections to nodes outside the result set.
        const filteredRelations = graph.relations.filter(r => filteredEntityNames.has(r.from) || filteredEntityNames.has(r.to));
        const filteredGraph = {
            entities: filteredEntities,
            relations: filteredRelations,
        };
        return filteredGraph;
    }
    async openNodes(names) {
        const graph = await this.loadGraph();
        // Filter entities
        const filteredEntities = graph.entities.filter(e => names.includes(e.name));
        // Create a Set of filtered entity names for quick lookup
        const filteredEntityNames = new Set(filteredEntities.map(e => e.name));
        // Include relations where at least one endpoint is in the requested set.
        // Previously this required BOTH endpoints, which meant relations from a
        // requested node to an unrequested node were silently dropped — making it
        // impossible to discover a node's connections without reading the full graph.
        const filteredRelations = graph.relations.filter(r => filteredEntityNames.has(r.from) || filteredEntityNames.has(r.to));
        const filteredGraph = {
            entities: filteredEntities,
            relations: filteredRelations,
        };
        return filteredGraph;
    }
}
// --- [포크] 대화별 그래프 라우팅 ---------------------------------------------
//
// 그래프는 `<GRAPH_DIR>/<graph_id>.jsonl` 파일 하나씩입니다.
let GRAPH_DIR;

// MEMORY_GRAPH_DIR 이 있으면 그것을, 없으면 기존 MEMORY_FILE_PATH 가 가리키던
// 파일 옆의 `.memory-graphs/` 를 씁니다.
export function resolveGraphDir(legacyMemoryFilePath) {
    const configured = (process.env.MEMORY_GRAPH_DIR ?? "").trim();
    if (configured) {
        return path.isAbsolute(configured)
            ? configured
            : path.join(path.dirname(fileURLToPath(import.meta.url)), configured);
    }
    return path.join(path.dirname(legacyMemoryFilePath), ".memory-graphs");
}

// 이 요청이 어느 그래프를 보는지를 담습니다. 전역 변수를 요청마다 갈아끼우면
// 두 대화의 호출이 await 지점에서 교차할 때 서로의 그래프에 씁니다. 그래서
// 값이 아니라 "요청 컨텍스트" 로 들고 있어야 합니다.
const currentGraph = new AsyncLocalStorage();

// 캐시하지 않습니다. KnowledgeGraphManager 는 파일 경로만 들고 있고 모든 연산이
// 매번 디스크에서 읽으므로, 들고 있어 봐야 대화 수만큼 쌓이기만 합니다.
export function managerFor(graphId) {
    return new KnowledgeGraphManager(path.join(GRAPH_DIR, `${graphId}.jsonl`));
}

// 스코프가 안 오면 공용 그래프가 아니라 이 프로세스 한정 그래프로 떨어집니다.
// 공용으로 떨어뜨리면 주입이 조용히 실패했을 때 예전처럼 대화가 섞이고, 아무도
// 눈치채지 못합니다. 파일 이름에 pid 가 남아 있으면 바로 알아볼 수 있습니다.
const FALLBACK_GRAPH_ID = `unscoped-${process.pid}`;
let warnedUnscoped = false;

const META_KEYS = [
    "graphId", "graph_id",
    "conversationId", "conversation_id",
    "threadId", "thread_id",
    "sessionId", "session_id",
];

export function sanitizeGraphId(raw) {
    if (typeof raw !== "string")
        return null;
    const id = raw.trim().replace(/[^A-Za-z0-9._-]/g, "-").replace(/^[.-]+/, "").slice(0, 64);
    return id || null;
}

// 우선순위: 요청 메타데이터 > 도구 인자 > 폴백.
// 메타데이터가 인자를 이깁니다. 스코프는 호스트가 아는 사실이고, 모델이
// graph_id 에 아무 값이나 적어도 남의 그래프를 열어서는 안 됩니다.
export function resolveGraphId(args, extra) {
    const meta = extra?._meta;
    if (meta) {
        for (const key of META_KEYS) {
            const id = sanitizeGraphId(meta[key]);
            if (id)
                return id;
        }
    }
    const fromArg = sanitizeGraphId(args?.graph_id);
    if (fromArg)
        return fromArg;
    if (!warnedUnscoped) {
        warnedUnscoped = true;
        console.error(`WARNING: request carried no graph scope; falling back to '${FALLBACK_GRAPH_ID}'. `
            + `The host should send _meta.conversationId (or a graph_id argument) on every call.`);
    }
    return FALLBACK_GRAPH_ID;
}

// 아래 도구 핸들러들은 원본 그대로 `knowledgeGraphManager.xxx()` 를 부릅니다.
// 그 이름이 "지금 요청의 그래프" 를 가리키도록 실체 대신 프록시를 둡니다.
const knowledgeGraphManager = new Proxy({}, {
    get(_target, prop) {
        const manager = currentGraph.getStore() ?? managerFor(FALLBACK_GRAPH_ID);
        const value = manager[prop];
        return typeof value === "function" ? value.bind(manager) : value;
    },
});

const GraphIdSchema = z.string().optional().describe("Isolated knowledge graph to read from and write to. The host injects this from the current "
    + "conversation, so omit it unless you were explicitly told which graph to use.");
// Zod schemas for entities and relations
const EntitySchema = z.object({
    name: z.string().describe("The name of the entity"),
    entityType: z.string().describe("The type of the entity"),
    observations: z.array(z.string()).describe("An array of observation contents associated with the entity")
});
const RelationSchema = z.object({
    from: z.string().describe("The name of the entity where the relation starts"),
    to: z.string().describe("The name of the entity where the relation ends"),
    relationType: z.string().describe("The type of the relation")
});
// The server instance and tools exposed to Claude
const server = new McpServer({
    name: "memory-server",
    version: "0.6.3",
});

// --- [포크] 등록기 래핑 -------------------------------------------------------
// 아래의 registerTool / registerResource 호출은 원본 그대로 둡니다. 대신 등록기를
// 감싸서 (1) 모든 도구 스키마에 graph_id 를 붙이고 (2) 핸들러를 그 요청의 그래프
// 컨텍스트 안에서 실행합니다.
const registerToolOriginal = server.registerTool.bind(server);
server.registerTool = (name, config, handler) => registerToolOriginal(name, { ...config, inputSchema: { ...(config.inputSchema ?? {}), graph_id: GraphIdSchema } }, (args, extra) => currentGraph.run(managerFor(resolveGraphId(args, extra)), () => handler(args, extra)));

const registerResourceOriginal = server.registerResource.bind(server);
server.registerResource = (name, uriOrTemplate, config, handler) => registerResourceOriginal(name, uriOrTemplate, config, (...callbackArgs) => {
    const extra = callbackArgs[callbackArgs.length - 1];
    return currentGraph.run(managerFor(resolveGraphId(null, extra)), () => handler(...callbackArgs));
});
const RESOURCE_URI = "memory://knowledge-graph";
// Track which resource URIs the connected client has subscribed to, so we only
// emit notifications/resources/updated to a client that asked for them.
const resourceSubscribers = new Set();
// Notify subscribers that the knowledge graph resource changed. No-op when the
// client has not subscribed.
function notifyGraphUpdated() {
    if (resourceSubscribers.has(RESOURCE_URI)) {
        server.server.sendResourceUpdated({ uri: RESOURCE_URI });
    }
}
// Register create_entities tool
server.registerTool("create_entities", {
    title: "Create Entities",
    description: "Create multiple new entities in the knowledge graph",
    inputSchema: {
        entities: z.array(EntitySchema)
    },
    outputSchema: {
        entities: z.array(EntitySchema)
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
    }
}, async ({ entities }) => {
    const result = await knowledgeGraphManager.createEntities(entities);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: { entities: result }
    };
});
// Register create_relations tool
server.registerTool("create_relations", {
    title: "Create Relations",
    description: "Create multiple new relations between entities in the knowledge graph. Relations should be in active voice",
    inputSchema: {
        relations: z.array(RelationSchema)
    },
    outputSchema: {
        relations: z.array(RelationSchema)
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
    }
}, async ({ relations }) => {
    const result = await knowledgeGraphManager.createRelations(relations);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: { relations: result }
    };
});
// Register add_observations tool
server.registerTool("add_observations", {
    title: "Add Observations",
    description: "Add new observations to existing entities in the knowledge graph",
    inputSchema: {
        observations: z.array(z.object({
            entityName: z.string().describe("The name of the entity to add the observations to"),
            contents: z.array(z.string()).describe("An array of observation contents to add")
        }))
    },
    outputSchema: {
        results: z.array(z.object({
            entityName: z.string(),
            addedObservations: z.array(z.string())
        }))
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false,
    }
}, async ({ observations }) => {
    const result = await knowledgeGraphManager.addObservations(observations);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        structuredContent: { results: result }
    };
});
// Register delete_entities tool
server.registerTool("delete_entities", {
    title: "Delete Entities",
    description: "Delete multiple entities and their associated relations from the knowledge graph",
    inputSchema: {
        entityNames: z.array(z.string()).describe("An array of entity names to delete")
    },
    outputSchema: {
        success: z.boolean(),
        message: z.string()
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async ({ entityNames }) => {
    await knowledgeGraphManager.deleteEntities(entityNames);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: "Entities deleted successfully" }],
        structuredContent: { success: true, message: "Entities deleted successfully" }
    };
});
// Register delete_observations tool
server.registerTool("delete_observations", {
    title: "Delete Observations",
    description: "Delete specific observations from entities in the knowledge graph",
    inputSchema: {
        deletions: z.array(z.object({
            entityName: z.string().describe("The name of the entity containing the observations"),
            observations: z.array(z.string()).describe("An array of observations to delete")
        }))
    },
    outputSchema: {
        success: z.boolean(),
        message: z.string()
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async ({ deletions }) => {
    await knowledgeGraphManager.deleteObservations(deletions);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: "Observations deleted successfully" }],
        structuredContent: { success: true, message: "Observations deleted successfully" }
    };
});
// Register delete_relations tool
server.registerTool("delete_relations", {
    title: "Delete Relations",
    description: "Delete multiple relations from the knowledge graph",
    inputSchema: {
        relations: z.array(RelationSchema).describe("An array of relations to delete")
    },
    outputSchema: {
        success: z.boolean(),
        message: z.string()
    },
    annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async ({ relations }) => {
    await knowledgeGraphManager.deleteRelations(relations);
    notifyGraphUpdated();
    return {
        content: [{ type: "text", text: "Relations deleted successfully" }],
        structuredContent: { success: true, message: "Relations deleted successfully" }
    };
});
// Register read_graph tool
server.registerTool("read_graph", {
    title: "Read Graph",
    description: "Read the entire knowledge graph",
    inputSchema: {},
    outputSchema: {
        entities: z.array(EntitySchema),
        relations: z.array(RelationSchema)
    },
    annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async () => {
    const graph = await knowledgeGraphManager.readGraph();
    return {
        content: [{ type: "text", text: JSON.stringify(graph, null, 2) }],
        structuredContent: { ...graph }
    };
});
// Register search_nodes tool
server.registerTool("search_nodes", {
    title: "Search Nodes",
    description: "Search for nodes in the knowledge graph based on a query",
    inputSchema: {
        query: z.string().describe("The search query to match against entity names, types, and observation content")
    },
    outputSchema: {
        entities: z.array(EntitySchema),
        relations: z.array(RelationSchema)
    },
    annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async ({ query }) => {
    const graph = await knowledgeGraphManager.searchNodes(query);
    return {
        content: [{ type: "text", text: JSON.stringify(graph, null, 2) }],
        structuredContent: { ...graph }
    };
});
// Register open_nodes tool
server.registerTool("open_nodes", {
    title: "Open Nodes",
    description: "Open specific nodes in the knowledge graph by their names",
    inputSchema: {
        names: z.array(z.string()).describe("An array of entity names to retrieve")
    },
    outputSchema: {
        entities: z.array(EntitySchema),
        relations: z.array(RelationSchema)
    },
    annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
    }
}, async ({ names }) => {
    const graph = await knowledgeGraphManager.openNodes(names);
    return {
        content: [{ type: "text", text: JSON.stringify(graph, null, 2) }],
        structuredContent: { ...graph }
    };
});
export function registerKnowledgeGraphResource(server, manager) {
    server.registerResource("knowledge-graph", RESOURCE_URI, {
        title: "Knowledge Graph",
        description: "The full knowledge graph with all entities and relations",
        mimeType: "application/json",
    }, async (uri) => {
        const graph = await manager.readGraph();
        return {
            contents: [
                {
                    uri: uri.href,
                    mimeType: "application/json",
                    text: JSON.stringify(graph, null, 2),
                },
            ],
        };
    });
}
// Enable clients to subscribe to the knowledge-graph resource and receive
// notifications/resources/updated when mutation tools change the graph.
export function registerKnowledgeGraphSubscriptions(server) {
    server.server.registerCapabilities({ resources: { subscribe: true } });
    server.server.setRequestHandler(SubscribeRequestSchema, async (request) => {
        resourceSubscribers.add(request.params.uri);
        return {};
    });
    server.server.setRequestHandler(UnsubscribeRequestSchema, async (request) => {
        resourceSubscribers.delete(request.params.uri);
        return {};
    });
}
async function main() {
    // [포크] 단일 그래프 파일 대신 그래프 디렉터리를 준비합니다. 어느 그래프를
    // 열지는 요청마다 정해집니다.
    GRAPH_DIR = resolveGraphDir(await ensureMemoryFilePath());
    await fs.mkdir(GRAPH_DIR, { recursive: true });
    registerKnowledgeGraphResource(server, knowledgeGraphManager);
    registerKnowledgeGraphSubscriptions(server);
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error(`Knowledge Graph MCP Server (per-conversation graphs) running on stdio; graphs in ${GRAPH_DIR}`);
}
main().catch((error) => {
    console.error("Fatal error in main():", error);
    process.exit(1);
});
