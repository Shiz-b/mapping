"use strict";
const TYPE_ORDER = ['research', 'tool', 'technical'];
const TYPE_LABEL = {
    research: 'RESEARCH',
    tool: 'TOOLS',
    technical: 'TECHNICAL',
};
const TYPE_COLOR = {
    research: '#F5A623',
    tool: '#4A9EFF',
    technical: '#2DD4A0',
};
const BASE_OPACITY = 1;
const FADED_OPACITY = 0.03;
const SEARCH_MATCH_COLOR = '#FF3B30';
const state = {
    research: { active: true, nodes: [], colors: [] },
    tool: { active: true, nodes: [], colors: [] },
    technical: { active: true, nodes: [], colors: [] },
};
const plotEl = document.getElementById('plot');
const controlsEl = document.getElementById('controls');
const tooltipEl = document.getElementById('tooltip');
const statsEl = document.getElementById('stats');
let pinnedNodeId = null;
let searchRequestId = 0;
function injectStyles() {
    const style = document.createElement('style');
    style.textContent = `
    #controls, #tooltip, #stats, .kg-pill, .kg-input-wrap {
      font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', monospace;
    }

    .kg-section-title {
      font-size: 10px;
      color: rgba(255,255,255,0.45);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 8px;
    }

    .kg-divider {
      border-top: 1px solid rgba(255,255,255,0.06);
      margin: 2px 0;
    }

    .kg-input-wrap {
      border-bottom: 1px solid rgba(255,255,255,0.15);
      transition: border-color 150ms ease;
      padding-bottom: 6px;
    }

    .kg-input-wrap:focus-within {
      border-bottom-color: rgba(255,255,255,0.40);
    }

    #kg-search {
      width: 100%;
      background: transparent;
      border: none;
      outline: none;
      appearance: none;
      color: rgba(255,255,255,0.7);
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: none;
    }

    #kg-search::placeholder {
      color: rgba(255,255,255,0.35);
      text-transform: none;
    }

    .kg-search-status {
      margin-top: 8px;
      min-height: 12px;
      font-size: 9px;
      letter-spacing: 0.08em;
      color: rgba(255,255,255,0.25);
      font-variant-numeric: tabular-nums;
    }

    .kg-pill-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .kg-pill {
      border-radius: 4px;
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      padding: 6px 8px;
      cursor: pointer;
      user-select: none;
      transition: all 150ms ease;
      border: 1px solid rgba(255,255,255,0.10);
      border-top: 1px solid rgba(255,255,255,0.18);
      background: rgba(255,255,255,0.04);
      color: rgba(255,255,255,0.35);
      backdrop-filter: blur(24px) saturate(180%);
      -webkit-backdrop-filter: blur(24px) saturate(180%);
      box-shadow: 0 0 0 0.5px rgba(255,255,255,0.05) inset, 0 8px 32px rgba(0,0,0,0.6);
    }

    .kg-pill:hover {
      background: rgba(255,255,255,0.07);
    }

    .kg-pill.active-research {
      background: rgba(245,166,35,0.15);
      border-color: rgba(245,166,35,0.40);
      color: rgba(245,166,35,0.90);
    }

    .kg-pill.active-tool {
      background: rgba(74,158,255,0.15);
      border-color: rgba(74,158,255,0.40);
      color: rgba(74,158,255,0.90);
    }

    .kg-pill.active-technical {
      background: rgba(45,212,160,0.15);
      border-color: rgba(45,212,160,0.40);
      color: rgba(45,212,160,0.90);
    }

    #tooltip {
      opacity: 0;
      transform: translateY(4px);
      transition: opacity 120ms ease, transform 120ms ease;
      color: rgba(255,255,255,0.9);
      top: 20px;
      right: 20px;
      left: auto !important;
      pointer-events: auto;
    }

    #tooltip.show {
      opacity: 1;
      transform: translateY(0);
    }

    .kg-tip-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 8px;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      margin-bottom: 10px;
      gap: 8px;
    }

    .kg-tip-badge {
      border-radius: 4px;
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      padding: 3px 6px;
      border: 1px solid transparent;
    }

    .kg-tip-coords {
      font-size: 9px;
      color: rgba(255,255,255,0.25);
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .kg-tip-title {
      font-size: 13px;
      font-weight: 500;
      color: rgba(255,255,255,0.90);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 8px;
      line-height: 1.3;
    }

    .kg-tip-desc {
      font-size: 11px;
      color: rgba(255,255,255,0.45);
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      overflow: hidden;
      text-overflow: ellipsis;
      line-height: 1.35;
      margin-bottom: 10px;
    }

    .kg-tip-foot {
      border-top: 1px solid rgba(255,255,255,0.06);
      padding-top: 8px;
      text-align: right;
    }

    .kg-tip-link {
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      text-decoration: none;
      pointer-events: auto;
    }
  `;
    document.head.appendChild(style);
}
function buildControls() {
    const searchSection = document.createElement('div');
    const searchTitle = document.createElement('div');
    searchTitle.className = 'kg-section-title';
    searchTitle.textContent = 'Search';
    const inputWrap = document.createElement('div');
    inputWrap.className = 'kg-input-wrap';
    const search = document.createElement('input');
    search.id = 'kg-search';
    search.placeholder = 'Search nodes';
    const searchStatus = document.createElement('div');
    searchStatus.className = 'kg-search-status';
    searchStatus.textContent = 'Ready';
    inputWrap.appendChild(search);
    searchSection.appendChild(searchTitle);
    searchSection.appendChild(inputWrap);
    searchSection.appendChild(searchStatus);
    const divider = document.createElement('div');
    divider.className = 'kg-divider';
    const filterSection = document.createElement('div');
    const filterTitle = document.createElement('div');
    filterTitle.className = 'kg-section-title';
    filterTitle.textContent = 'Type Filters';
    const row = document.createElement('div');
    row.className = 'kg-pill-row';
    const pills = {};
    TYPE_ORDER.forEach((type) => {
        const button = document.createElement('button');
        button.className = `kg-pill active-${type}`;
        button.textContent = TYPE_LABEL[type];
        row.appendChild(button);
        pills[type] = button;
    });
    filterSection.appendChild(filterTitle);
    filterSection.appendChild(row);
    controlsEl.appendChild(searchSection);
    controlsEl.appendChild(divider);
    controlsEl.appendChild(filterSection);
    return { search, searchStatus, pills };
}
function groupNodes(nodes) {
    TYPE_ORDER.forEach((type) => {
        state[type].nodes = nodes.filter((n) => n.type === type);
        state[type].colors = new Array(state[type].nodes.length).fill(withAlpha(TYPE_COLOR[type], BASE_OPACITY));
    });
}
function withAlpha(hexColor, alpha) {
    const rgb = hexColor
        .replace('#', '')
        .match(/.{1,2}/g)
        ?.map((v) => parseInt(v, 16)) ?? [255, 255, 255];
    return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`;
}
function buildTraces() {
    return TYPE_ORDER.map((type) => {
        const nodes = state[type].nodes;
        return {
            type: 'scatter3d',
            mode: 'markers',
            name: TYPE_LABEL[type],
            x: nodes.map((n) => n.coords.x),
            y: nodes.map((n) => n.coords.y),
            z: nodes.map((n) => n.coords.z),
            text: nodes.map((n) => n.title),
            customdata: nodes.map((n) => n),
            hoverinfo: 'none',
            marker: {
                size: 2,
                color: state[type].colors,
                symbol: 'circle',
            },
            visible: true,
        };
    });
}
function layoutPlot() {
    return {
        margin: { l: 0, r: 0, t: 0, b: 0 },
        paper_bgcolor: '#000000',
        plot_bgcolor: '#000000',
        scene: {
            bgcolor: '#000000',
            xaxis: { showticklabels: false, showgrid: false, zeroline: false, showline: false, title: '' },
            yaxis: { showticklabels: false, showgrid: false, zeroline: false, showline: false, title: '' },
            zaxis: { showticklabels: false, showgrid: false, zeroline: false, showline: false, title: '' },
            camera: {
                eye: { x: 1.3, y: 1.3, z: 1.1 },
            },
        },
        showlegend: false,
    };
}
function updateStats() {
    const visibleCounts = TYPE_ORDER.map((type) => (state[type].active ? state[type].nodes.length : 0));
    const total = visibleCounts.reduce((acc, n) => acc + n, 0);
    statsEl.textContent = `${visibleCounts[0]} RESEARCH · ${visibleCounts[1]} TOOLS · ${visibleCounts[2]} TECHNICAL · ${total} TOTAL`;
}
async function applySearch(query) {
    const q = query.trim().toLowerCase();
    let totalMatches = 0;
    const restylePromises = [];
    TYPE_ORDER.forEach((type, idx) => {
        const colors = state[type].nodes.map((node) => {
            if (!q) {
                totalMatches += 1;
                return withAlpha(TYPE_COLOR[type], BASE_OPACITY);
            }
            const haystack = `${node.title} ${node.description}`.toLowerCase();
            const isMatch = haystack.includes(q);
            if (isMatch) {
                totalMatches += 1;
            }
            return isMatch
                ? withAlpha(SEARCH_MATCH_COLOR, BASE_OPACITY)
                : withAlpha(TYPE_COLOR[type], FADED_OPACITY);
        });
        state[type].colors = colors;
        restylePromises.push(Promise.resolve(Plotly.restyle(plotEl, { 'marker.color': [colors] }, [idx])));
    });
    await Promise.all(restylePromises);
    return totalMatches;
}
function toggleType(type, button) {
    state[type].active = !state[type].active;
    if (state[type].active) {
        button.classList.add(`active-${type}`);
    }
    else {
        button.classList.remove(`active-${type}`);
    }
    const traceIndex = TYPE_ORDER.indexOf(type);
    Plotly.restyle(plotEl, { visible: [state[type].active] }, [traceIndex]);
    updateStats();
}
function formatCoord(value) {
    return value.toFixed(4);
}
function badgeStyle(type) {
    const color = TYPE_COLOR[type];
    const rgb = color
        .replace('#', '')
        .match(/.{1,2}/g)
        ?.map((v) => parseInt(v, 16)) ?? [255, 255, 255];
    return `background: rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.15); color: rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.9); border-color: rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.4);`;
}
function showTooltip(node) {
    const clippedDescription = node.description.length > 160 ? `${node.description.slice(0, 157)}...` : node.description;
    tooltipEl.innerHTML = `
    <div class="kg-tip-head">
      <span class="kg-tip-badge" style="${badgeStyle(node.type)}">${TYPE_LABEL[node.type]}</span>
      <span class="kg-tip-coords">x: ${formatCoord(node.coords.x)} y: ${formatCoord(node.coords.y)} z: ${formatCoord(node.coords.z)}</span>
    </div>
    <div class="kg-tip-title">${node.title}</div>
    <div class="kg-tip-desc">${clippedDescription}</div>
    <div class="kg-tip-foot">
      <a class="kg-tip-link" style="color: ${TYPE_COLOR[node.type]}" href="${node.url}" target="_blank" rel="noopener noreferrer">OPEN ↗</a>
    </div>
  `;
    tooltipEl.style.display = 'block';
    tooltipEl.classList.add('show');
}
function hideTooltip() {
    tooltipEl.classList.remove('show');
    tooltipEl.style.display = 'none';
    tooltipEl.innerHTML = '';
}
function clearPinnedTooltip() {
    pinnedNodeId = null;
    hideTooltip();
}
async function init() {
    injectStyles();
    const { search, searchStatus, pills } = buildControls();
    const response = await fetch('./data/nodes.json');
    if (!response.ok) {
        throw new Error(`Failed to load data/nodes.json: ${response.status}`);
    }
    const nodes = (await response.json());
    groupNodes(nodes);
    await Plotly.newPlot(plotEl, buildTraces(), layoutPlot(), {
        displayModeBar: false,
        responsive: true,
    });
    updateStats();
    search.addEventListener('input', () => {
        const currentRequestId = ++searchRequestId;
        searchStatus.textContent = 'Searching...';
        window.setTimeout(async () => {
            try {
                const matches = await applySearch(search.value);
                if (currentRequestId !== searchRequestId) {
                    return;
                }
                const hasQuery = search.value.trim().length > 0;
                searchStatus.textContent = hasQuery ? `${matches} nodes highlighted` : 'Showing all nodes';
            }
            catch {
                if (currentRequestId === searchRequestId) {
                    searchStatus.textContent = 'Search failed';
                }
            }
        }, 0);
    });
    TYPE_ORDER.forEach((type) => {
        pills[type].addEventListener('click', () => toggleType(type, pills[type]));
    });
    plotEl.on('plotly_hover', (event) => {
        if (pinnedNodeId) {
            return;
        }
        const point = event?.points?.[0];
        if (!point?.customdata) {
            return;
        }
        const node = point.customdata;
        showTooltip(node);
    });
    plotEl.on('plotly_unhover', () => {
        if (pinnedNodeId) {
            return;
        }
        hideTooltip();
    });
    plotEl.on('plotly_click', (event) => {
        const point = event?.points?.[0];
        if (!point?.customdata) {
            return;
        }
        const node = point.customdata;
        if (pinnedNodeId === node.id) {
            clearPinnedTooltip();
            return;
        }
        pinnedNodeId = node.id;
        showTooltip(node);
    });
    document.addEventListener('click', (event) => {
        const target = event.target;
        if (target.closest('#plot') || target.closest('#tooltip')) {
            return;
        }
        if (pinnedNodeId) {
            clearPinnedTooltip();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && pinnedNodeId) {
            clearPinnedTooltip();
        }
    });
    window.addEventListener('resize', () => {
        Plotly.Plots.resize(plotEl);
    });
}
init().catch((error) => {
    console.error(error);
    statsEl.textContent = 'FAILED TO LOAD DATA';
});
