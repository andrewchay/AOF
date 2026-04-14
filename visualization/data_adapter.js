/**
 * AOF 知识图谱可视化数据适配器
 * 
 * 采用适配器模式设计，支持多种后端数据源：
 * - LegacyAdapter: 使用现有 /v1/datasets/{id}/data 接口（快速启动）
 * - GraphAPIAdapter: 使用专用 /v1/graph/* 接口（高性能）
 * - CogneeAdapter: 直接连接 Cognee 引擎（调试模式）
 */

// ==================== 抽象基类 ====================

class GraphDataAdapter {
    constructor(baseUrl = 'http://localhost:8787') {
        this.baseUrl = baseUrl;
        this.datasetName = 'default';
    }

    setDataset(name) {
        this.datasetName = name;
    }

    // 子类必须实现的方法
    async fetchGraphData() {
        throw new Error('必须实现 fetchGraphData 方法');
    }

    async fetchNodeById(nodeId) {
        throw new Error('必须实现 fetchNodeById 方法');
    }

    async searchNodes(query, options = {}) {
        throw new Error('必须实现 searchNodes 方法');
    }

    async fetchStatistics() {
        throw new Error('必须实现 fetchStatistics 方法');
    }

    // 通用数据转换方法
    transformToVisualizationFormat(nodes, edges, options = {}) {
        const categories = new Map();
        const categoryColors = [
            '#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#F44336',
            '#00BCD4', '#FFEB3B', '#795548', '#607D8B', '#E91E63',
            '#3F51B5', '#009688', '#CDDC39', '#FF5722', '#9E9E9E'
        ];

        const visNodes = nodes.map((node, index) => {
            const category = this.extractCategory(node);
            
            if (!categories.has(category)) {
                categories.set(category, {
                    name: category,
                    color: categoryColors[categories.size % categoryColors.length],
                    icon: this.getCategoryIcon(category),
                    count: 0
                });
            }
            categories.get(category).count++;

            return {
                id: node.id || `node_${index}`,
                name: this.extractNodeName(node),
                category: category,
                color: categories.get(category).color,
                icon: categories.get(category).icon,
                radius: this.calculateNodeRadius(node),
                properties: node.properties || node,
                labels: node.labels || [category],
                x: node.x,
                y: node.y
            };
        });

        const visLinks = edges.map((edge, index) => ({
            id: edge.id || `edge_${index}`,
            source: edge.source_id || edge.source,
            target: edge.target_id || edge.target,
            type: edge.relation_type || edge.type || '相关',
            strength: edge.properties?.weight || edge.weight || 0.5,
            properties: edge.properties || edge
        }));

        return {
            nodes: visNodes,
            links: visLinks,
            categories: Array.from(categories.values())
        };
    }

    extractCategory(node) {
        return node.labels?.[0] || node.category || node.type || '其他';
    }

    extractNodeName(node) {
        return node.properties?.name 
            || node.properties?.title 
            || node.name 
            || node.title 
            || `节点 ${node.id}`;
    }

    calculateNodeRadius(node) {
        const baseRadius = 5;
        const importance = node.properties?.importance 
            || node.importance 
            || 0.5;
        const connections = node.properties?.degree 
            || node.degree 
            || 1;
        return baseRadius + importance * 5 + Math.log(connections + 1) * 2;
    }

    getCategoryIcon(category) {
        const iconMap = {
            '游戏内容_任务': '📜', '游戏内容_版本': '📅', '概念': '💡',
            '分析技能': '🧠', '游戏内容_道具养成': '💎', '增长分析_指标': '📊',
            '其他': '📌', '游戏内容_副本': '⚔️', '游戏内容_角色': '🎭',
            '文档': '📄', '数据源': '🗄️', '游戏内容_活动': '🎁',
            '增长分析_策略': '🎯', '平台': '🖥️', '分析技能_方法': '🔧',
            '分析技能_原则': '📐', 'Person': '👤', 'Organization': '🏢',
            'Location': '📍', 'Event': '📅', 'Document': '📄', 'Concept': '💡',
            'Task': '✅', 'Goal': '🎯', 'Skill': '🔧'
        };
        return iconMap[category] || '📦';
    }
}

// ==================== 方案 A: 使用现有 API ====================

class LegacyAdapter extends GraphDataAdapter {
    /**
     * 使用现有的 /v1/datasets/{id}/data 接口
     * 
     * 优点：无需改动后端，快速启动
     * 局限：大数据量时性能受限，不支持增量更新
     */
    
    async fetchGraphData(options = {}) {
        const { limit = 10000, offset = 0 } = options;
        
        try {
            // 调用 AOF 现有的数据集数据接口
            const response = await fetch(
                `${this.baseUrl}/v1/datasets/${this.datasetName}/data?` +
                `limit=${limit}&offset=${offset}`
            );
            
            if (!response.ok) {
                // 如果新接口不存在，尝试旧的数据获取方式
                return await this.fetchFromLegacyEndpoint();
            }
            
            const data = await response.json();
            
            // 适配不同可能的响应格式
            const nodes = data.nodes || data.entities || data.vertices || [];
            const edges = data.edges || data.relations || data.links || [];
            
            // 如果数据格式是 Cognee 的，需要额外处理
            if (data.cognee_data) {
                return this.transformCogneeData(data.cognee_data);
            }
            
            return this.transformToVisualizationFormat(nodes, edges);
            
        } catch (error) {
            console.error('获取图谱数据失败:', error);
            throw error;
        }
    }

    async fetchFromLegacyEndpoint() {
        // 备选方案：从可视化列表接口获取
        const response = await fetch(
            `${this.baseUrl}/v1/visualize/list?dataset=${this.datasetName}`
        );
        
        if (!response.ok) {
            throw new Error('无法获取图谱数据');
        }
        
        const visualizations = await response.json();
        if (visualizations.length === 0) {
            throw new Error('该数据集没有可视化数据');
        }
        
        // 获取最新的可视化文件数据
        const latest = visualizations[0];
        const dataResponse = await fetch(latest.data_url);
        const data = await dataResponse.json();
        
        return this.transformToVisualizationFormat(
            data.nodes || [], 
            data.edges || []
        );
    }

    transformCogneeData(cogneeData) {
        // 转换 Cognee 数据格式
        const nodes = [];
        const edges = [];
        const nodeMap = new Map();

        // 处理 Cognee 的节点
        if (cogneeData.nodes) {
            cogneeData.nodes.forEach((node, index) => {
                const id = node.id || `node_${index}`;
                nodeMap.set(id, node);
                nodes.push({
                    id: id,
                    name: node.name || node.text || `节点 ${index}`,
                    labels: node.type ? [node.type] : ['Node'],
                    properties: node
                });
            });
        }

        // 处理 Cognee 的边
        if (cogneeData.edges) {
            cogneeData.edges.forEach(edge => {
                edges.push({
                    source_id: edge.source || edge.from,
                    target_id: edge.target || edge.to,
                    relation_type: edge.type || edge.label || '相关',
                    properties: edge
                });
            });
        }

        return this.transformToVisualizationFormat(nodes, edges);
    }

    async fetchNodeById(nodeId) {
        // 全量获取后在前端查找
        const data = await this.fetchGraphData();
        return data.nodes.find(n => n.id === nodeId);
    }

    async searchNodes(query, options = {}) {
        const { limit = 20 } = options;
        const data = await this.fetchGraphData();
        
        const results = data.nodes.filter(node => 
            node.name.toLowerCase().includes(query.toLowerCase()) ||
            node.category.toLowerCase().includes(query.toLowerCase())
        ).slice(0, limit);
        
        return results;
    }

    async fetchStatistics() {
        try {
            // 尝试使用现有的统计接口
            const response = await fetch(
                `${this.baseUrl}/v1/analytics/statistics?dataset=${this.datasetName}`
            );
            
            if (response.ok) {
                return await response.json();
            }
            
            // 回退：从数据计算统计
            const data = await this.fetchGraphData();
            return {
                node_count: data.nodes.length,
                edge_count: data.links.length,
                category_count: data.categories.length
            };
        } catch (error) {
            console.error('获取统计失败:', error);
            return { node_count: 0, edge_count: 0, category_count: 0 };
        }
    }

    // 性能优化：分页加载
    async *fetchGraphDataStream(options = {}) {
        const { batchSize = 1000 } = options;
        let offset = 0;
        let hasMore = true;

        while (hasMore) {
            const batch = await this.fetchGraphData({
                limit: batchSize,
                offset
            });

            if (batch.nodes.length === 0) {
                hasMore = false;
            } else {
                yield batch;
                offset += batchSize;
            }

            // 防止无限循环
            if (offset > 100000) {
                console.warn('达到最大加载限制');
                break;
            }
        }
    }
}

// ==================== 方案 B: 专用 Graph API ====================

class GraphAPIAdapter extends GraphDataAdapter {
    /**
     * 使用专用的 /v1/graph/* 接口
     * 
     * 优点：高性能、支持分页、增量更新、复杂查询
     * 局限：需要后端实现对应接口
     */

    async fetchGraphData(options = {}) {
        const { 
            limit = 1000, 
            offset = 0,
            categories = [],
            includeProperties = true 
        } = options;

        // 并行获取节点和边
        const [nodesRes, edgesRes] = await Promise.all([
            fetch(`${this.baseUrl}/v1/graph/nodes?` + new URLSearchParams({
                dataset: this.datasetName,
                limit: String(limit),
                offset: String(offset),
                categories: categories.join(','),
                include_properties: String(includeProperties)
            })),
            fetch(`${this.baseUrl}/v1/graph/edges?` + new URLSearchParams({
                dataset: this.datasetName,
                limit: String(limit * 2), // 边通常比节点多
                offset: String(offset)
            }))
        ]);

        if (!nodesRes.ok || !edgesRes.ok) {
            throw new Error('Graph API 调用失败');
        }

        const nodes = await nodesRes.json();
        const edges = await edgesRes.json();

        return this.transformToVisualizationFormat(nodes, edges);
    }

    async fetchNodeById(nodeId) {
        const response = await fetch(
            `${this.baseUrl}/v1/graph/nodes/${nodeId}?dataset=${this.datasetName}`
        );
        if (!response.ok) throw new Error('获取节点失败');
        return await response.json();
    }

    async fetchNeighbors(nodeId, options = {}) {
        const { depth = 1, limit = 50 } = options;
        
        const response = await fetch(
            `${this.baseUrl}/v1/graph/nodes/${nodeId}/neighbors?` + new URLSearchParams({
                dataset: this.datasetName,
                depth: String(depth),
                limit: String(limit)
            })
        );

        if (!response.ok) throw new Error('获取邻居失败');
        const data = await response.json();
        
        return this.transformToVisualizationFormat(
            data.nodes || [], 
            data.edges || []
        );
    }

    async searchNodes(query, options = {}) {
        const { limit = 20, fuzzy = true } = options;
        
        const response = await fetch(`${this.baseUrl}/v1/graph/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dataset: this.datasetName,
                query,
                limit,
                fuzzy
            })
        });

        if (!response.ok) throw new Error('搜索失败');
        return await response.json();
    }

    async fetchStatistics() {
        const response = await fetch(
            `${this.baseUrl}/v1/graph/statistics?dataset=${this.datasetName}`
        );
        if (!response.ok) throw new Error('获取统计失败');
        return await response.json();
    }

    async fetchCategories() {
        const response = await fetch(
            `${this.baseUrl}/v1/graph/categories?dataset=${this.datasetName}`
        );
        if (!response.ok) throw new Error('获取分类失败');
        return await response.json();
    }

    // 高级查询：执行 Cypher/nGQL
    async executeQuery(query, parameters = {}) {
        const response = await fetch(`${this.baseUrl}/v1/graph/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                dataset: this.datasetName,
                query,
                parameters
            })
        });

        if (!response.ok) throw new Error('查询执行失败');
        return await response.json();
    }

    // 增量更新：获取自上次更新后的变化
    async fetchDelta(lastUpdateTime) {
        const response = await fetch(
            `${this.baseUrl}/v1/graph/delta?` + new URLSearchParams({
                dataset: this.datasetName,
                since: lastUpdateTime.toISOString()
            })
        );

        if (!response.ok) throw new Error('获取增量数据失败');
        return await response.json();
    }
}

// ==================== 工厂函数 ====================

function createAdapter(type = 'auto', baseUrl = 'http://localhost:8787') {
    if (type === 'legacy') {
        return new LegacyAdapter(baseUrl);
    } else if (type === 'graph_api') {
        return new GraphAPIAdapter(baseUrl);
    } else if (type === 'auto') {
        // 自动检测：尝试 Graph API，失败则回退到 Legacy
        return new AutoDetectAdapter(baseUrl);
    }
    throw new Error(`未知的适配器类型: ${type}`);
}

class AutoDetectAdapter extends GraphDataAdapter {
    constructor(baseUrl) {
        super(baseUrl);
        this.adapter = null;
        this.detectedType = null;
    }

    async getAdapter() {
        if (this.adapter) return this.adapter;

        // 尝试检测 Graph API 是否可用
        try {
            const response = await fetch(
                `${this.baseUrl}/v1/graph/health`,
                { method: 'HEAD', timeout: 2000 }
            );
            
            if (response.ok) {
                console.log('检测到 Graph API，使用高性能适配器');
                this.adapter = new GraphAPIAdapter(this.baseUrl);
                this.detectedType = 'graph_api';
            } else {
                throw new Error('Graph API 不可用');
            }
        } catch (error) {
            console.log('Graph API 不可用，回退到 Legacy 适配器');
            this.adapter = new LegacyAdapter(this.baseUrl);
            this.detectedType = 'legacy';
        }

        return this.adapter;
    }

    async fetchGraphData(options = {}) {
        const adapter = await this.getAdapter();
        return adapter.fetchGraphData(options);
    }

    async fetchNodeById(nodeId) {
        const adapter = await this.getAdapter();
        return adapter.fetchNodeById(nodeId);
    }

    async searchNodes(query, options = {}) {
        const adapter = await this.getAdapter();
        return adapter.searchNodes(query, options);
    }

    async fetchStatistics() {
        const adapter = await this.getAdapter();
        return adapter.fetchStatistics();
    }
}

// ==================== 使用示例 ====================

/**
 * 初始化可视化
 * 
 * 使用方法：
 * 1. 自动检测（推荐）：const adapter = createAdapter('auto');
 * 2. 强制使用现有 API：const adapter = createAdapter('legacy');
 * 3. 使用专用 API：const adapter = createAdapter('graph_api');
 */
async function initWithRealData() {
    // 创建适配器（自动检测后端类型）
    const adapter = createAdapter('auto', 'http://localhost:8787');
    adapter.setDataset('my_dataset');

    try {
        // 加载数据
        const data = await adapter.fetchGraphData();
        
        // 更新全局状态
        state.nodes = data.nodes;
        state.links = data.links;
        
        // 更新分类
        state.categories.clear();
        data.categories.forEach(cat => {
            state.categories.set(cat.name, cat);
            state.selectedCategories.add(cat.name);
        });
        
        // 重新初始化 UI 和可视化
        initUI();
        updateVisualization();
        
        console.log('数据加载完成，使用的适配器:', adapter.detectedType || 'legacy');
        
    } catch (error) {
        console.error('初始化失败:', error);
        // 回退到模拟数据
        alert('无法连接到 AOF 后端，将使用模拟数据');
        init();
    }
}

/**
 * 搜索并聚焦
 */
async function searchAndFocus(query) {
    const adapter = createAdapter('auto');
    adapter.setDataset(state.datasetName || 'default');
    
    try {
        const results = await adapter.searchNodes(query, { limit: 10 });
        
        if (results.length > 0) {
            // 高亮搜索结果
            highlightNodes(results.map(r => r.id));
            
            // 如果只有一个结果，聚焦它
            if (results.length === 1) {
                focusOnNeighbors(results[0].id);
            }
        }
    } catch (error) {
        console.error('搜索失败:', error);
    }
}

/**
 * 增量更新（用于实时同步）
 */
async function incrementalUpdate(lastUpdateTime) {
    const adapter = createAdapter('auto');
    
    // 只有 GraphAPIAdapter 支持增量更新
    if (adapter instanceof GraphAPIAdapter) {
        const delta = await adapter.fetchDelta(lastUpdateTime);
        
        // 应用增量更新
        delta.added_nodes?.forEach(node => state.nodes.push(node));
        delta.removed_nodes?.forEach(nodeId => {
            state.nodes = state.nodes.filter(n => n.id !== nodeId);
        });
        
        updateVisualization();
    }
}

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        GraphDataAdapter,
        LegacyAdapter,
        GraphAPIAdapter,
        AutoDetectAdapter,
        createAdapter,
        initWithRealData
    };
}
