let currentAgent = 'supervisor';
let currentSession = null;
const taskList = document.getElementById('task-list');
const logs = document.getElementById('logs');
const cmdInput = document.getElementById('cmd-input');
const targetIdEl = document.getElementById('target-id');


function activateSkill(name) {
    if (cmdInput) {
        let currentText = cmdInput.value;
        const skillPattern = /^skill \S+\s*/;
        if (skillPattern.test(currentText)) {
            currentText = currentText.replace(skillPattern, '');
        }
        cmdInput.value = `skill ${name} ` + currentText;
        cmdInput.focus();
        appendLog('SYSTEM', `Skill [${name}] loaded.`, 'text-blue-500 font-bold');
        fetch('/api/v2/skills/use', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ skill_name: name }) }).catch(() => { });
    }
}

function switchAgent(name) {
    currentAgent = name;
    currentSession = null;
    targetIdEl.innerHTML = `<i data-lucide="crosshair" class="w-3.5 h-3.5 mr-2 text-blue-500"></i> TARGET: ${name.toUpperCase()}`;
    if (typeof lucide !== 'undefined') lucide.createIcons();

    document.querySelectorAll('#agent-list > div').forEach(el => {
        el.classList.remove('agent-active');
        const scroller = el.querySelector('.agent-sessions');
        if (scroller) scroller.classList.add('hidden');
    });

    const activeEl = document.getElementById('agent-' + name);
    if (activeEl) {
        activeEl.classList.add('agent-active');
        const scroller = activeEl.querySelector('.agent-sessions');
        if (scroller) {
            scroller.classList.remove('hidden');
            const firstBtn = scroller.querySelector('.session-btn');
            if (firstBtn) firstBtn.click();
        }
    }
    
    // Update dynamic skills filter
    if (typeof updateSkillUI === 'function') {
        updateSkillUI();
    }
    appendLog('SYSTEM', 'Entity focus: ' + name, 'text-blue-600');
}

function selectSession(agent, session, event) {
    if (event) event.stopPropagation();
    currentAgent = agent;
    currentSession = session;

    const container = document.getElementById(`agent-${agent}`);
    if (container) {
        container.querySelectorAll('.session-btn').forEach(b => {
            b.className = 'session-btn text-[9px] font-bold px-2.5 py-1.5 rounded-lg bg-gray-50 text-[#86868b] border border-gray-100 hover:bg-gray-100 transition-all uppercase tracking-wider shadow-sm';
        });
    }

    if (event && event.currentTarget) {
        event.currentTarget.className = 'session-btn text-[9px] font-bold px-2.5 py-1.5 rounded-lg bg-blue-50 text-blue-600 border border-blue-100 transition-all uppercase tracking-wider shadow-sm';
    }

    targetIdEl.innerHTML = `<i data-lucide="crosshair" class="w-3.5 h-3.5 mr-2 text-blue-500"></i> TARGET: ${agent.toUpperCase()} <span class="text-gray-300 mx-1">/</span> ${session}`;
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function appendLog(from, msg, color = 'text-[#424245]') {
    const div = document.createElement('div');
    div.className = 'log-item ' + color;
    div.innerHTML = `<span class="text-[9px] block uppercase font-bold text-[#86868b] mb-1">${from} <span class="font-normal opacity-60">· ${new Date().toLocaleTimeString()}</span></span>${msg}`;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
}

function appendAgentReply(agentId, content, status = 'SUCCESS') {
    const div = document.createElement('div');
    const accentColor = status === 'SUCCESS' ? 'blue-500' : 'red-500';
    const bgColor = status === 'SUCCESS' ? 'bg-blue-50/30' : 'bg-red-50/30';
    const isLong = content.length > 400;
    const shortContent = isLong ? content.slice(0, 400) + '...' : content;
    const uid = 'reply-' + Date.now();

    div.className = 'mb-4';
    div.innerHTML = `
        <div class="rounded-2xl border border-gray-100 bg-white overflow-hidden shadow-sm">
            <div class="flex items-center justify-between px-4 py-2.5 ${bgColor} border-b border-gray-50">
                <span class="text-[10px] font-bold uppercase tracking-widest text-${accentColor}">${agentId} Response</span>
                <span class="text-[9px] text-gray-400 font-medium">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="p-4 text-[11px] text-[#424245] leading-relaxed whitespace-pre-wrap font-mono" id="${uid}-short">${shortContent.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
            ${isLong ? `
                <div class="p-4 text-[11px] text-[#424245] leading-relaxed whitespace-pre-wrap font-mono hidden" id="${uid}-full">${content.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>
                <div class="px-4 pb-3">
                    <button onclick="document.getElementById('${uid}-short').classList.toggle('hidden'); document.getElementById('${uid}-full').classList.toggle('hidden'); this.textContent = this.textContent === 'Read More' ? 'Collapse' : 'Read More';" 
                        class="text-[10px] text-blue-600 font-bold hover:text-blue-800 transition-colors cursor-pointer">Read More</button>
                </div>
            ` : ''}
        </div>
    `;
    logs.appendChild(div);
    logs.scrollTop = logs.scrollHeight;
}

let refreshTimer = null;
function refreshTasks() {
    if (refreshTimer) return;
    refreshTimer = setTimeout(() => {
        performRefreshTasks();
        refreshTimer = null;
    }, 300);
}

async function performRefreshTasks() {
    try {
        const res = await fetch('/api/v2/tasks');
        const data = await res.json();
        taskList.innerHTML = '';

        const lanes = {
            'PENDING': { label: 'PENDING', color: 'border-gray-200 text-gray-400', tasks: [] },
            'RUNNING': { label: 'RUNNING', color: 'border-blue-500 text-blue-500', tasks: [] },
            'DONE': { label: 'DONE', color: 'border-emerald-500 text-emerald-500', tasks: [] },
            'CANCELLED': { label: 'FAILED', color: 'border-red-500 text-red-500', tasks: [] }
        };

        data.forEach(t => {
            if (t.status === 'FAILED' || t.status === 'CANCELLED' || t.status === 'PAUSING' || t.status === 'CANCELLING') {
                lanes['CANCELLED'].tasks.push(t);
            } else if (t.status === 'DONE') {
                lanes['DONE'].tasks.push(t);
            } else if (t.status === 'RUNNING') {
                lanes['RUNNING'].tasks.push(t);
            } else {
                lanes['PENDING'].tasks.push(t);
            }
        });

        const board = document.createElement('div');
        board.className = 'flex lg:grid lg:grid-cols-4 gap-6 h-full overflow-x-auto lg:overflow-x-visible snap-x pb-4 scrollbar-hide';

        for (const [key, lane] of Object.entries(lanes)) {
            const col = document.createElement('div');
            col.className = `flex flex-col min-w-[280px] lg:min-w-0 snap-center shrink-0 lg:shrink`;
            col.innerHTML = `
                <div class="text-[10px] font-bold text-[#86868b] dark:text-slate-400 uppercase tracking-[0.2em] mb-6 pb-2 border-b-2 ${lane.color} flex justify-between items-center transition-colors">
                    <span>${lane.label}</span>
                    <span class="text-[10px] bg-gray-100 dark:bg-slate-800 text-[#1d1d1f] dark:text-slate-50 px-2 py-0.5 rounded-full transition-colors font-bold shadow-sm">${lane.tasks.length}</span>
                </div>
            `;

            const scrollArea = document.createElement('div');
            scrollArea.className = 'flex-1 overflow-y-auto space-y-4 scrollbar-hide';

            lane.tasks.forEach(t => {
                let sourceBadge = '';
                try {
                    const ctx = typeof t.context === 'string' ? JSON.parse(t.context) : t.context;
                    if (ctx && ctx.source) {
                        const src = ctx.source.toUpperCase();
                        sourceBadge = `<span class="text-[8px] bg-gray-50 text-gray-500 px-1.5 py-0.5 rounded-md border border-gray-100 font-bold ml-2">${src}</span>`;
                    }
                } catch (err) { }


                let stepsSummary = '';
                if (t.steps && t.steps.length > 0) {
                    const lastStep = t.steps[t.steps.length - 1];
                    const stepColor = lastStep.status === 'FAILED' ? 'text-red-600 bg-red-50 dark:bg-red-900/40 dark:text-red-400' : lastStep.status === 'DONE' ? 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/40 dark:text-emerald-400' : 'text-blue-600 bg-blue-50 dark:bg-blue-900/40 dark:text-blue-400';
                    stepsSummary = `<div class="text-[9px] ${stepColor} mt-3 px-3 py-2 rounded-xl font-bold tracking-tight truncate">${lastStep.name.toUpperCase()}</div>`;
                }

                let statusBadgeColor = 'bg-gray-100 text-gray-500 dark:bg-slate-800 dark:text-slate-400';
                if (t.status === 'DONE') statusBadgeColor = 'bg-emerald-50 text-emerald-600 border border-emerald-100 dark:bg-emerald-900/30 dark:border-emerald-800/50 dark:text-emerald-400';
                else if (t.status === 'FAILED' || t.status === 'CANCELLED') statusBadgeColor = 'bg-red-50 text-red-600 border border-red-100 dark:bg-red-900/30 dark:border-red-800/50 dark:text-red-400';
                else if (t.status === 'RUNNING') statusBadgeColor = 'bg-blue-50 text-blue-600 border border-blue-100 dark:bg-blue-900/30 dark:border-blue-800/50 dark:text-blue-400 animate-pulse';

                const card = document.createElement('div');
                card.className = `bg-white dark:bg-slate-800/40 p-5 rounded-2xl border border-gray-100 dark:border-white/5 shadow-sm transition-all duration-300 hover:shadow-md hover:border-blue-200 dark:hover:border-blue-400 cursor-pointer group`;

                card.innerHTML = `
                    <div class="flex justify-between items-start mb-2">
                        <div class="flex-1 min-w-0">
                            <div class="text-xs font-bold text-[#1d1d1f] dark:text-slate-50 leading-snug break-words">${t.title}${sourceBadge}</div>
                            <div class="text-[9px] text-[#86868b] dark:text-slate-400 mt-1.5 font-semibold uppercase tracking-widest">${t.assignee} <span class="text-gray-300 dark:text-slate-600 mx-1">·</span> #${t.id.substring(0, 6)}</div>
                        </div>
                        <div class="flex flex-col items-end gap-1 ml-2">
                            <span class="text-[9px] font-bold px-1.5 py-0.5 rounded-md ${statusBadgeColor}">${t.status}</span>
                            <span class="text-[10px] font-bold text-blue-600 dark:text-blue-400">${t.progress || 0}%</span>
                        </div>
                    </div>
                    
                    ${t.status === 'RUNNING' ? `
                        <div class="w-full bg-gray-100 dark:bg-slate-800 h-1.5 rounded-full mt-4 overflow-hidden shadow-inner dark:border dark:border-white/5">
                            <div class="shimmer-bar h-full rounded-full transition-all duration-700 ease-out" style="width:${t.progress || 0}%"></div>
                        </div>
                    ` : '<div class="h-2"></div>'}
                    
                    ${auditMode === 'manual' && t.status !== 'DONE' && t.status !== 'CANCELLED' && t.status !== 'FAILED' ? `
                        <div class="mt-3 flex gap-2 border-t border-gray-50 dark:border-white/5 pt-3">
                            <button onclick="updateTaskStatus('${t.id}', 'DONE', event)" class="flex-1 px-2 py-1 bg-emerald-50 text-emerald-600 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40 rounded text-[9px] font-bold transition-colors">✔ 标记完成</button>
                            <button onclick="updateTaskStatus('${t.id}', 'CANCELLED', event)" class="flex-1 px-2 py-1 bg-red-50 text-red-600 hover:bg-red-100 dark:bg-red-900/20 dark:text-red-400 dark:hover:bg-red-900/40 rounded text-[9px] font-bold transition-colors">✖ 取消任务</button>
                        </div>
                    ` : ''}
                `;

                scrollArea.appendChild(card);
            });
            col.appendChild(scrollArea);
            board.appendChild(col);
        }
        taskList.appendChild(board);
    } catch (e) {
        console.error("Refresh failed:", e);
    }
}

function openHaltModal() {
    const modal = document.getElementById('immersive-halt-modal');
    if (!modal) return;
    modal.style.display = 'block';
    setTimeout(() => {
        modal.classList.remove('pointer-events-none', 'opacity-0');
        modal.classList.add('opacity-100');
    }, 10);
}

function abortHalt() {
    const modal = document.getElementById('immersive-halt-modal');
    if (!modal) return;
    modal.classList.remove('opacity-100');
    modal.classList.add('opacity-0');
    setTimeout(() => {
        modal.classList.add('pointer-events-none');
        modal.style.display = 'none';
    }, 300);
}

async function executeHalt() {
    VOICE_SYSTEM.play('task_halt');
    try {
        const res = await fetch('/api/v2/tasks', { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            appendLog('SYSTEM', 'EMERGENCY HALT EXECUTED', 'text-red-600 font-bold');
            performRefreshTasks();
            abortHalt();
        }
    } catch (e) {
        console.error("Halt failed", e);
    }
}

async function clearAllTasks() {
    openHaltModal();
}

async function updateTaskStatus(taskId, status, event) {
    if (event) event.stopPropagation();
    try {
        const progress = status === 'DONE' ? 100 : 0;
        const res = await fetch(`/api/v2/tasks/${taskId}/progress`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ progress: progress, status: status })
        });
        const data = await res.json();
        if (data.success) {
            appendLog('SYSTEM', `Task ${taskId.substring(0, 6)} status updated to ${status}`, 'text-blue-500 font-bold');
            refreshTasks();
        } else {
            appendLog('ERROR', `Failed to update task status: ${data.error}`, 'text-red-500 font-bold');
        }
    } catch (e) {
        console.error("Update task status failed", e);
    }
}

async function launchTask(name) {
    appendLog('DIRECTIVE', name, 'text-blue-600 font-bold');
    try {
        const reqBody = { target: currentAgent, prompt: name, audit_mode: auditMode };
        if (currentSession) reqBody.agent_session = currentSession;

        const response = await fetch('/api/v2/commands', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(reqBody)
        });

        const data = await response.json();
        if (data.error) appendLog('ERROR', data.error, 'text-red-600');
        refreshTasks();
    } catch (e) {
        appendLog('ERROR', "Nexus connection failed.", 'text-red-600');
    }
}

if (cmdInput) {
    let suggestTimer = null;
    let selectedSugIdx = -1;

    cmdInput.addEventListener('keydown', (e) => {
        // CJK IME composition detection
        if (e.isComposing || e.keyCode === 229) return;

        const sugBox = document.getElementById('cmd-suggestions');
        const sugList = document.getElementById('cmd-suggestions-list');
        const items = sugList ? sugList.querySelectorAll('.cmd-sug-item') : [];

        // Arrow navigation in suggestions
        if (!sugBox?.classList.contains('hidden') && items.length > 0) {
            if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedSugIdx = Math.min(selectedSugIdx + 1, items.length - 1);
                highlightSuggestion(items, selectedSugIdx);
                return;
            }
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedSugIdx = Math.max(selectedSugIdx - 1, -1);
                if (selectedSugIdx === -1) {
                    highlightSuggestion(items, -1);
                } else {
                    highlightSuggestion(items, selectedSugIdx);
                }
                return;
            }
            if (e.key === 'Enter' && selectedSugIdx >= 0) {
                e.preventDefault();
                items[selectedSugIdx].click();
                return;
            }
            if (e.key === 'Escape') {
                hideSuggestions();
                return;
            }
        }

        if (e.key === 'Enter') {
            e.preventDefault();
            const val = cmdInput.value.trim();
            if (val) {
                launchTask(val);
                cmdInput.value = '';
                hideSuggestions();
            }
        }
    });

    cmdInput.addEventListener('input', () => {
        clearTimeout(suggestTimer);
        const val = cmdInput.value.trim();

        // Don't suggest if already using a skill prefix or empty
        if (!val || val.startsWith('skill ')) {
            hideSuggestions();
            return;
        }

        suggestTimer = setTimeout(() => fetchSuggestions(val), 200);
    });

    cmdInput.addEventListener('blur', () => {
        // Delay to allow click on suggestion
        setTimeout(hideSuggestions, 200);
    });
}

async function fetchSuggestions(query) {
    try {
        const res = await fetch(`/api/v2/skills/suggest?q=${encodeURIComponent(query)}`);
        const suggestions = await res.json();
        const sugBox = document.getElementById('cmd-suggestions');
        const sugList = document.getElementById('cmd-suggestions-list');
        if (!sugBox || !sugList) return;

        if (!suggestions || suggestions.length === 0) {
            hideSuggestions();
            return;
        }

        selectedSugIdx = -1;
        sugList.innerHTML = '';
        const isDark = document.documentElement.classList.contains('dark');

        suggestions.forEach((s, i) => {
            const item = document.createElement('div');
            item.className = `cmd-sug-item flex items-center justify-between px-4 py-2.5 rounded-xl cursor-pointer transition-all text-left ${isDark ? 'hover:bg-slate-800/60' : 'hover:bg-amber-50'}`;
            item.innerHTML = `
                <div class="flex-1 min-w-0">
                    <div class="text-sm font-bold ${isDark ? 'text-slate-50' : 'text-stone-800'} truncate">${s.name}</div>
                    <div class="text-[10px] ${isDark ? 'text-slate-400' : 'text-stone-500'} truncate">${s.description || s.category || ''}</div>
                </div>
                ${s.use_count > 0 ? `<span class="shrink-0 ml-2 text-[8px] font-bold ${isDark ? 'bg-blue-900/30 text-blue-400 border-blue-800/40' : 'bg-orange-50 text-orange-600 border-orange-100'} px-1.5 py-0.5 rounded-full border">${s.use_count}×</span>` : ''}
            `;
            item.onclick = () => {
                activateSkill(s.name);
                hideSuggestions();
            };
            sugList.appendChild(item);
        });

        sugBox.classList.remove('hidden');
    } catch (e) { }
}

function hideSuggestions() {
    const sugBox = document.getElementById('cmd-suggestions');
    if (sugBox) sugBox.classList.add('hidden');
    selectedSugIdx = -1;
}

function highlightSuggestion(items, idx) {
    const isDark = document.documentElement.classList.contains('dark');
    items.forEach((el, i) => {
        if (i === idx) {
            el.classList.add(isDark ? 'bg-slate-800/60' : 'bg-amber-50');
        } else {
            el.classList.remove('bg-slate-800/60', 'bg-amber-50');
        }
    });
}

let auditMode = 'agent';
async function fetchSystemConfig() {
    try {
        const res = await fetch('/api/v2/system/config');
        const data = await res.json();
        if (data.audit_mode) { auditMode = data.audit_mode; updateAuditUI(); }
    } catch (e) { }
}

function updateAuditUI() {
    const label = document.getElementById('audit-mode-label');
    const dot = document.getElementById('audit-toggle-dot');
    if (label && dot) {
        if (auditMode === 'agent') {
            label.textContent = 'AI AUDIT ON';
            label.className = 'mr-2 text-blue-600 dark:text-blue-400 font-bold transition-colors';
            dot.className = 'w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse transition-colors';
        } else {
            label.textContent = 'DIRECT EXEC';
            label.className = 'mr-2 text-gray-500 dark:text-slate-400 font-bold transition-colors';
            dot.className = 'w-1.5 h-1.5 rounded-full bg-gray-300 dark:bg-slate-600 transition-colors';
        }
    }
}

async function toggleAuditMode() {
    const newMode = auditMode === 'agent' ? 'manual' : 'agent';
    try {
        const res = await fetch('/api/v2/system/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audit_mode: newMode })
        });
        const data = await res.json();
        if (data.audit_mode) { auditMode = data.audit_mode; updateAuditUI(); }
    } catch (e) { }
}

let _allSkillsCache = [];

async function fetchSkills() {
    try {
        const res = await fetch('/api/skills');
        const categories = await res.json();
        _allSkillsCache = [];

        for (const [cat, skills] of Object.entries(categories)) {
            skills.forEach(s => {
                _allSkillsCache.push({ ...s, category: cat });
            });
        }
        
        if (typeof updateSkillUI === 'function') {
            updateSkillUI();
        }
    } catch (e) { console.error('fetchSkills error:', e); }
}

function updateSkillUI() {
    // Filter skills based on currentAgent
    const visibleSkills = _allSkillsCache.filter(s => {
        if (currentAgent === 'supervisor') return true;
        if (!s.agents) return true; // fallback
        return s.agents.includes(currentAgent);
    });
    
    const countLabel = document.getElementById('skill-count-label');
    if (countLabel) countLabel.textContent = `${visibleSkills.length} Skills Available`;
    const panelCount = document.getElementById('skill-panel-count');
    if (panelCount) panelCount.textContent = `${visibleSkills.length} skills available for ${currentAgent}`;

    // Populate Hot Skills
    const hotList = document.getElementById('skill-hot-list');
    if (hotList) {
        const hotSkills = [...visibleSkills].sort((a, b) => (b.use_count || 0) - (a.use_count || 0)).slice(0, 6);
        hotList.innerHTML = '';
        hotSkills.forEach(s => {
            const chip = document.createElement('button');
            chip.onclick = () => { activateSkill(s.name); toggleSkillPanel(); };
            chip.className = 'shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 border border-orange-100 dark:border-orange-800/30 hover:bg-orange-100 dark:hover:bg-orange-900/40 transition-all cursor-pointer whitespace-nowrap';
            chip.innerHTML = `<span>${s.name}</span>${s.use_count > 0 ? `<span class="bg-orange-200 dark:bg-orange-800/50 text-orange-700 dark:text-orange-300 px-1.5 py-0.5 rounded-full text-[8px]">${s.use_count}</span>` : ''}`;
            hotList.appendChild(chip);
        });
    }

    renderSkillGrid(visibleSkills);
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

function renderSkillGrid(skills) {
    const grid = document.getElementById('skill-directory');
    if (!grid) return;
    grid.innerHTML = '';

    // Group by category
    const grouped = {};
    skills.forEach(s => {
        if (!grouped[s.category]) grouped[s.category] = [];
        grouped[s.category].push(s);
    });

    for (const [cat, catSkills] of Object.entries(grouped)) {
        // Category header
        const header = document.createElement('div');
        header.className = 'col-span-full';
        header.innerHTML = `<div class="text-[10px] font-bold text-[#86868b] dark:text-slate-500 uppercase tracking-widest mb-1 mt-2">${cat}</div>`;
        grid.appendChild(header);

        catSkills.forEach(s => {
            const btn = document.createElement('button');
            btn.onclick = () => { activateSkill(s.name); toggleSkillPanel(); };
            btn.className = 'skill-btn p-4 rounded-2xl text-left transition-all cursor-pointer relative group';
            const useBadge = s.use_count > 0
                ? `<span class="absolute top-3 right-3 text-[8px] font-bold bg-blue-50 dark:bg-blue-900/30 text-blue-500 dark:text-blue-400 px-1.5 py-0.5 rounded-full border border-blue-100 dark:border-blue-800/40">${s.use_count}×</span>`
                : '';
            btn.innerHTML = `${useBadge}<div class="font-bold text-sm mb-1 text-[#1d1d1f] dark:text-slate-50 transition-colors pr-8">${s.name}</div><div class="text-[10px] text-[#86868b] dark:text-slate-400 transition-colors leading-relaxed line-clamp-2">${s.description || 'No description available.'}</div>`;
            grid.appendChild(btn);
        });
    }
}

function filterSkills(query) {
    const q = query.toLowerCase().trim();
    const hotSection = document.getElementById('skill-hot-section');

    const visibleSkills = _allSkillsCache.filter(s => {
        if (currentAgent === 'supervisor') return true;
        if (!s.agents) return true;
        return s.agents.includes(currentAgent);
    });

    if (!q) {
        if (hotSection) hotSection.style.display = '';
        renderSkillGrid(visibleSkills);
        return;
    }

    // Hide hot section during search
    if (hotSection) hotSection.style.display = 'none';

    const filtered = visibleSkills.filter(s =>
        s.name.toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q) ||
        s.category.toLowerCase().includes(q)
    );
    renderSkillGrid(filtered);
}

let skillPanelOpen = false;
function toggleSkillPanel() {
    const overlay = document.getElementById('skill-panel-overlay');
    const panel = document.getElementById('skill-panel');
    if (!overlay || !panel) return;
    skillPanelOpen = !skillPanelOpen;
    if (skillPanelOpen) {
        overlay.style.display = 'block';
        setTimeout(() => {
            overlay.style.opacity = '1';
            panel.classList.remove('translate-y-full');
        }, 10);
        // Re-init icons for newly added Lucide icons
        setTimeout(() => { if (typeof lucide !== 'undefined') lucide.createIcons(); }, 50);
    } else {
        overlay.style.opacity = '0';
        panel.classList.add('translate-y-full');
        setTimeout(() => { overlay.style.display = 'none'; }, 500);
        // Reset search state
        const searchInput = document.getElementById('skill-search-input');
        if (searchInput) searchInput.value = '';
        const hotSection = document.getElementById('skill-hot-section');
        if (hotSection) hotSection.style.display = '';
        renderSkillGrid(_allSkillsCache);
    }
}

async function fetchAgentStatus() {
    try {
        const res = await fetch('/api/v2/agents');
        const data = await res.json();
        let connectedMap = {};
        data.forEach(a => connectedMap[a.id] = a.status);

        ['gemini-bot', 'openclaw-bridge', 'deepseek-nas'].forEach(agentId => {
            const el = document.getElementById('agent-' + agentId);
            if (el) {
                const badge = el.querySelector('.agent-status-badge');
                if (badge) {
                    if (connectedMap[agentId] === 'ONLINE') {
                        badge.className = 'agent-status-badge text-[9px] font-bold bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 px-2.5 py-1 rounded-full border border-emerald-100 dark:border-emerald-800/50 transition-colors';
                        badge.textContent = 'ONLINE';
                    } else {
                        badge.className = 'agent-status-badge text-[9px] font-bold bg-gray-50 dark:bg-slate-800 text-gray-400 dark:text-slate-500 px-2.5 py-1 rounded-full border border-gray-100 dark:border-white/5 transition-colors';
                        badge.textContent = 'OFFLINE';
                    }
                }
            }
        });
    } catch (e) { }
}

// ========== DOTA2 Voice Pack System (多英雄语音包) ==========
const HERO_PACKS = {
    cm:   { id: 'cm',   name: '冰女',     nameEn: 'Crystal Maiden',    color: '#4FC3F7', darkColor: '#81D4FA', dir: 'ref1_crystalmaiden',     img: '/images/heroes/cm.png' },
    drow: { id: 'drow', name: '小黑',     nameEn: 'Drow Ranger',       color: '#1565C0', darkColor: '#42A5F5', dir: 'ref2_drowranger',        img: '/images/heroes/drow.png' },
    void: { id: 'void', name: '虚空',     nameEn: 'Faceless Void',     color: '#7B2D8B', darkColor: '#BA68C8', dir: 'ref3_faceless_void',     img: '/images/heroes/void.png' },
    lina: { id: 'lina', name: '火女',     nameEn: 'Lina',              color: '#E53935', darkColor: '#EF5350', dir: 'ref4_lina',              img: '/images/heroes/lina.png' },
    qop:  { id: 'qop',  name: '痛苦女王', nameEn: 'Queen of Pain',     color: '#AD1457', darkColor: '#EC407A', dir: 'ref5_queenofpain',       img: '/images/heroes/qop.png' },
    ta:   { id: 'ta',   name: '圣堂刺客', nameEn: 'Templar Assassin',  color: '#9C27B0', darkColor: '#CE93D8', dir: 'ref6_templar_assassin',  img: '/images/heroes/ta.png' },
};

const SOUND_EVENTS = ['task_created', 'task_running', 'task_done', 'task_failed', 'task_halt', 'agent_online', 'nudge'];

const VOICE_SYSTEM = {
    enabled: true,
    activeHero: null,   // null = TTS only, heroId = use that pack
    _audioCache: {},     // { heroId: { EVENT: Audio } }
    _ttsVoice: null,

    // TTS 回退文案
    ttsLines: {
        task_created:  '新任务已部署，准备战斗！',
        task_running:  '任务启动，特工已进入战场！',
        task_done:     '任务完成！干得漂亮！',
        task_failed:   '任务失败！请求增援！',
        task_halt:     '全线撤退！紧急停机！',
        agent_online:  '一名盟友已连接！',
        nudge:         '警告！特工失联，审计官已介入！',
    },

    init() {
        // TTS voice
        if ('speechSynthesis' in window) {
            const pickVoice = () => {
                const voices = speechSynthesis.getVoices();
                this._ttsVoice = voices.find(v => v.lang.startsWith('zh') && v.name.includes('Tingting'))
                    || voices.find(v => v.lang.startsWith('zh'))
                    || voices[0];
            };
            pickVoice();
            speechSynthesis.onvoiceschanged = pickVoice;
        }
        // Restore saved state
        const saved = localStorage.getItem('voice_enabled');
        if (saved === '0') this.enabled = false;
        const savedHero = localStorage.getItem('voice_hero');
        if (savedHero && HERO_PACKS[savedHero]) this.activeHero = savedHero;
        // Preload active hero
        if (this.activeHero) this._preloadHero(this.activeHero);
    },

    _preloadHero(heroId) {
        if (this._audioCache[heroId]) return;
        const pack = HERO_PACKS[heroId];
        if (!pack) return;
        this._audioCache[heroId] = {};
        SOUND_EVENTS.forEach(evt => {
            const audio = new Audio();
            audio.preload = 'auto';
            audio.src = `/audio/dota_voices/${pack.dir}/${evt}.wav`;
            audio.addEventListener('canplaythrough', () => { this._audioCache[heroId][evt] = audio; });
            audio.addEventListener('error', () => { /* file missing, will fallback to TTS */ });
        });
    },

    play(eventType) {
        if (!this.enabled) return;
        const evtKey = eventType.toLowerCase();
        // Try hero audio
        if (this.activeHero && this._audioCache[this.activeHero] && this._audioCache[this.activeHero][evtKey]) {
            const clone = this._audioCache[this.activeHero][evtKey].cloneNode();
            clone.volume = 0.65;
            clone.play().catch(() => {});
            return;
        }
        // TTS fallback removed
    },

    toggleEnabled() {
        this.enabled = !this.enabled;
        localStorage.setItem('voice_enabled', this.enabled ? '1' : '0');
        return this.enabled;
    },

    selectHero(heroId) {
        if (heroId && !HERO_PACKS[heroId]) return;
        this.activeHero = heroId;
        localStorage.setItem('voice_hero', heroId || '');
        if (heroId) {
            this.enabled = true;
            localStorage.setItem('voice_enabled', '1');
            this._preloadHero(heroId);
        }
        updateVoiceUI();
    }
};

// ========== Voice Pack Selector UI ==========
let _voicePanelOpen = false;

function toggleVoicePanel() {
    const overlay = document.getElementById('voice-panel-overlay');
    if (!overlay) return;
    _voicePanelOpen = !_voicePanelOpen;
    if (_voicePanelOpen) {
        overlay.style.display = 'block';
        requestAnimationFrame(() => {
            overlay.style.opacity = '1';
            overlay.querySelector('.voice-panel-body')?.classList.remove('translate-y-full');
        });
    } else {
        overlay.style.opacity = '0';
        overlay.querySelector('.voice-panel-body')?.classList.add('translate-y-full');
        setTimeout(() => { overlay.style.display = 'none'; }, 400);
    }
}

function selectVoiceHero(heroId) {
    VOICE_SYSTEM.selectHero(heroId);
    VOICE_SYSTEM.play('agent_online');
    // Close panel after short delay to let user see the selection
    setTimeout(() => { if (_voicePanelOpen) toggleVoicePanel(); }, 600);
}

function disableVoice() {
    VOICE_SYSTEM.selectHero(null);
    VOICE_SYSTEM.enabled = false;
    localStorage.setItem('voice_enabled', '0');
    updateVoiceUI();
    if (_voicePanelOpen) toggleVoicePanel();
}

function updateVoiceUI() {
    const label = document.getElementById('voice-label');
    const avatar = document.getElementById('voice-avatar');
    const dot = document.getElementById('voice-dot');
    const fallbackIcon = document.getElementById('voice-icon-fallback');
    
    if (!label) return;

    if (VOICE_SYSTEM.enabled && VOICE_SYSTEM.activeHero) {
        const hero = HERO_PACKS[VOICE_SYSTEM.activeHero];
        label.textContent = hero.name;
        if (avatar) {
            avatar.src = hero.img;
            avatar.style.display = 'block';
            if (fallbackIcon) fallbackIcon.style.display = 'none';
            avatar.onerror = function() { 
                this.style.display = 'none'; 
                if (fallbackIcon) fallbackIcon.style.display = 'block';
            };
        }
        if (dot) { 
            dot.className = 'absolute -top-1 -right-1 w-3 h-3 rounded-full bg-emerald-500 ring-2 ring-white dark:ring-slate-900 animate-pulse z-10'; 
        }
    } else {
        label.textContent = VOICE_SYSTEM.enabled ? 'TTS' : 'OFF';
        if (avatar) avatar.style.display = 'none';
        if (fallbackIcon) fallbackIcon.style.display = 'block';
        if (dot) { 
            const color = VOICE_SYSTEM.enabled ? 'bg-amber-500' : 'bg-gray-400';
            dot.className = `absolute -top-1 -right-1 w-3 h-3 rounded-full ${color} ring-2 ring-white dark:ring-slate-900 z-10`; 
        }
    }

    // Update hero cards active state in panel
    document.querySelectorAll('.hero-voice-card').forEach(card => {
        const hid = card.dataset.hero;
        card.classList.toggle('hero-voice-active', hid === VOICE_SYSTEM.activeHero);
    });
}

let ws;
function connectWS() {
    ws = new WebSocket('ws://' + window.location.host + '/ws/dashboard');
    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (data.type === 'agent_reply') appendAgentReply(data.agent_id, data.content, data.status);
            else if (data.type === 'TASK_CREATED') {
                VOICE_SYSTEM.play('task_created');
                refreshTasks();
            }
            else if (data.type === 'TASK_UPDATED') {
                const changes = data.changes || {};
                if (changes.status === 'RUNNING') VOICE_SYSTEM.play('task_running');
                else if (changes.status === 'DONE') VOICE_SYSTEM.play('task_done');
                else if (changes.status === 'FAILED' || changes.status === 'CANCELLED') VOICE_SYSTEM.play('task_failed');
                refreshTasks();
            }
            else if (data.type === 'STEP_UPDATED') refreshTasks();
            else if (data.type === 'system') {
                if (data.content && data.content.includes('审计')) VOICE_SYSTEM.play('nudge');
                appendLog('SYSTEM', data.content, 'text-blue-500');
            }
        } catch (e) { }
    };
    ws.onclose = () => setTimeout(connectWS, 3000);
}

document.addEventListener('DOMContentLoaded', () => {
    VOICE_SYSTEM.init();
    updateVoiceUI();
    fetchSystemConfig();
    performRefreshTasks();
    fetchSkills();
    fetchAgentStatus();
    connectWS();
    setInterval(refreshTasks, 5000);
    setInterval(fetchAgentStatus, 10000);
});

// Handle Theme
function toggleTheme() {
    if (document.documentElement.classList.contains('dark')) {
        document.documentElement.classList.remove('dark');
        localStorage.theme = 'light';
    } else {
        document.documentElement.classList.add('dark');
        localStorage.theme = 'dark';
    }
}
// Init theme
if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
} else {
    document.documentElement.classList.remove('dark');
}
