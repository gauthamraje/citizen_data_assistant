/**
 * Option 4 — step-by-step solutions flow, blueprint drawer, PDF export.
 * Ported from Civic Mentor Bot recipe pattern.
 */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'cda_solutions_state_v1';
    const MIN_FULL_PLAN_CHARS = 120;

    const OPTION4_FLOW_HINT =
        'OPTION 4 TURN A (mission picker only, ~90 words). Use ONLY missions from search results. Present exactly **2** missions: "**1. [Action_Title]**" + one Story_Context sentence, "**2. ...**". Ask which feels doable—1 or 2? FORBIDDEN: Survey/Observe/Interview, Option 3, any steps/scripts/officials.';

    const OPTION4_FIRST_WIN_HINT =
        'OPTION 4 TURN B (first win only, ~80 words). User picked a mission. Celebrate briefly. Give ONE micro-action for today (~15-20 min) from the first part of that mission\'s Action_Steps. One line how a Ninja started (Story_Context). Time-box it. End: say "full plan" for scripts/officials. FORBIDDEN: full Action_Steps list, Communication_Script, "---", Download PDF, multiple steps.';

    const OPTION4_FULL_PLAN_HINT =
        'OPTION 4 TURN C (full DIY). Use the chosen mission from search results. Markdown: intro + **Primary_Goal**, blank line, "---", numbered Action_Steps + Communication_Script + Problem_Solving_Tips. End ONLY: Tap **Download PDF** below. FORBIDDEN: dense paragraph, Survey/Investigation.';

    const MISSION_PICK_CHIPS = [
        { label: '① Mission 1', value: '1' },
        { label: '② Mission 2', value: '2' },
        { label: '✗ Neither fits', value: 'Neither of these fit — show me two other missions from the library' },
    ];

    const FULL_PLAN_CHIPS = [
        { label: '📋 Show full plan + PDF', value: 'Show me the full plan with PDF' },
        { label: "✓ I'm good for now", value: "Yes, I'm good to start with this step" },
    ];

    const EXPLORE_CHIPS = [
        { label: "✓ Let's explore this", value: "Let's explore this" },
        { label: '🔄 Other missions', value: 'Show me two other missions from the library' },
    ];

    const PDF_ICON =
        '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M12 3v12m0 0l4-4m-4 4l-4-4M4 19h16"></path></svg>';
    const VIEW_ICON =
        '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>';

    let state = loadState();
    let currentBlueprintMarkdown = '';
    let pendingFlowHint = null;
    let onRenderChips = null;
    let onToggleDrawer = null;
    let onUpdateRecipeBar = null;

    function defaultState() {
        return { turn: 'picker', pickedMission: null, lastOfferedSingleMission: false };
    }

    function loadState() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            return raw ? { ...defaultState(), ...JSON.parse(raw) } : defaultState();
        } catch {
            return defaultState();
        }
    }

    function saveState() {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
        updateRecipeBar();
    }

    function resetState() {
        state = defaultState();
        saveState();
    }

    function activate() {
        resetState();
        updateRecipeBar();
    }

    function recipeStepIndex() {
        const map = { picker: 0, awaiting_first_win: 1, first_win: 1, awaiting_full_plan: 2, full_plan: 2, done: 3 };
        return map[state.turn] ?? 0;
    }

    function updateRecipeBar() {
        if (onUpdateRecipeBar) onUpdateRecipeBar(recipeStepIndex());
    }

    function configure(hooks) {
        onRenderChips = hooks.renderChips || null;
        onToggleDrawer = hooks.toggleDrawer || null;
        onUpdateRecipeBar = hooks.updateRecipeBar || null;
    }

    function stripCitations(text) {
        return (text || '').replace(/【\d+:\d+†source】/g, '').replace(/【[^】]*†[^】]*】/g, '');
    }

    function normalizeMarkdown(text) {
        let s = stripCitations(text).replace(/\r\n/g, '\n');
        s = s.replace(/([.:!?])\s+(\d+\.\s+\*?\*?)/g, '$1\n\n$2');
        s = s.replace(/([^\n])\s+(\d+\.\s+[A-Z])/g, '$1\n\n$2');
        s = s.replace(/:\s+-\s/g, ':\n- ');
        s = s.replace(/\s+(Tap \*\*Download PDF\*\*[^\n]*)/gi, '\n\n$1');
        return s.trim();
    }

    function renderMarkdown(content, introOnly) {
        const md = introOnly
            ? normalizeMarkdown((content || '').split('---')[0])
            : normalizeMarkdown(content);
        if (!md) return '';
        return typeof marked !== 'undefined' ? marked.parse(md) : md;
    }

    function hasTechnicalBlueprint(content) {
        const parts = (content || '').split('---');
        return parts.length > 1 && parts.slice(1).join('---').trim().length > 40;
    }

    function hasTwoMissionList(text) {
        if (!text) return false;
        const has1 =
            /\*\*1[\.\)]/.test(text) ||
            /\b1\.\s+\*\*/.test(text) ||
            /^1[\.\)]\s/m.test(text) ||
            /\n1[\.\)]\s/.test(text);
        const has2 =
            /\*\*2[\.\)]/.test(text) ||
            /\b2\.\s+\*\*/.test(text) ||
            /^2[\.\)]\s/m.test(text) ||
            /\n2[\.\)]\s/.test(text);
        return has1 && has2;
    }

    function isMissionPickerPrompt(text) {
        const t = (text || '').toLowerCase();
        if (hasTwoMissionList(text)) return true;
        const asksPick =
            t.includes('which feels') ||
            t.includes('most doable') ||
            t.includes('pick') ||
            t.includes('choose') ||
            t.includes('1 or 2') ||
            t.includes('mission 1') ||
            t.includes('option 1');
        const hasMission =
            /\*\*1[\.\)]/.test(text) ||
            /\b1\.\s+\*\*/.test(text) ||
            t.includes('**1.') ||
            (t.includes('mission') && t.includes('1'));
        return asksPick && hasMission;
    }

    function isSingleMissionOffer(text) {
        if (isMissionPickerPrompt(text)) return false;
        const t = (text || '').toLowerCase();
        const hasOnlyFirst = /\*\*1[\.\)]/.test(text) && !/\*\*2[\.\)]/.test(text);
        return (
            hasOnlyFirst ||
            (t.includes('mission') &&
                (t.includes('recommend') || t.includes('suggest') || t.includes('fits')))
        );
    }

    function isMissionPick(userText) {
        const t = userText.trim();
        if (/^[12]$/.test(t)) return true;
        const lower = t.toLowerCase();
        return (
            (lower.includes('mission') &&
                (lower.includes('pick') || lower.includes('option') || lower.includes('number'))) ||
            lower.includes('feels most doable') ||
            lower === 'option 1' ||
            lower === 'option 2'
        );
    }

    function isMissionExploreCommit(userText) {
        const lower = userText.trim().toLowerCase();
        return (
            /^let'?s\s+explore/.test(lower) ||
            lower.includes("let's explore") ||
            lower.includes('lets explore') ||
            lower.includes('explore this') ||
            lower.includes('go with this') ||
            lower.includes('start with this')
        );
    }

    function isMissionRejection(userText) {
        const lower = userText.toLowerCase();
        return (
            lower.includes('other mission') ||
            lower.includes('different mission') ||
            lower.includes('something else') ||
            lower.includes('another mission') ||
            lower.includes('neither') ||
            lower.includes('none of these') ||
            lower.includes('neither of these') ||
            lower.includes("doesn't fit") ||
            lower.includes('dont fit') ||
            lower.includes("don't fit") ||
            lower.includes('not relatable') ||
            lower.includes('show me two other')
        );
    }

    function isFullPlanRequest(userText) {
        const lower = userText.toLowerCase();
        return (
            lower.includes('full plan') ||
            lower.includes('full diy') ||
            lower.includes('show me the full') ||
            lower.includes('full blueprint')
        );
    }

    function isGoodForNow(userText) {
        const lower = userText.toLowerCase();
        return lower.includes('good for now') || lower.includes('good to start with this step');
    }

    function isShortThanks(userText) {
        const t = userText.trim().toLowerCase();
        return /^(thanks?|thank you|thx|ok|okay|great|got it)\.?!?$/i.test(t);
    }

    function isLikelyClosingReply(text) {
        const t = (text || '').toLowerCase();
        return t.includes("you're welcome") || (t.includes('glad') && t.includes('help'));
    }

    function beginMissionFirstWin(userText) {
        state.turn = 'awaiting_first_win';
        state.pickedMission = userText.trim();
        state.lastOfferedSingleMission = true;
        saveState();
    }

    function getFlowHintForTurn(userText) {
        if (isShortThanks(userText) && ['full_plan', 'first_win', 'done'].includes(state.turn)) {
            state.turn = 'done';
            saveState();
            return 'OPTION 4: User said thanks. Reply warmly in 2 sentences. No full plan, no PDF, no mission dump.';
        }
        if (isFullPlanRequest(userText)) {
            state.turn = 'awaiting_full_plan';
            saveState();
            return OPTION4_FULL_PLAN_HINT;
        }
        if (isGoodForNow(userText)) {
            state.turn = 'done';
            saveState();
            return 'OPTION 4: User is satisfied with first win. Encourage warmly in 2 sentences. No full plan unless they ask later.';
        }
        if (isMissionRejection(userText)) {
            state.turn = 'picker';
            state.lastOfferedSingleMission = false;
            saveState();
            return 'OPTION 4 TURN A (refine). User rejected earlier missions — they need NEW options. Search the library again and present exactly **2** DIFFERENT missions (**1.** **2.** + one Story_Context sentence each). Ask which feels doable—1 or 2? No steps yet.';
        }
        if (isMissionPick(userText) || isMissionExploreCommit(userText)) {
            beginMissionFirstWin(userText);
            const extra = isMissionExploreCommit(userText)
                ? ' User committed to the recommended mission. Use the mission you just offered.'
                : '';
            return OPTION4_FIRST_WIN_HINT + extra;
        }
        return OPTION4_FLOW_HINT;
    }

    function consumePendingHint() {
        const hint = pendingFlowHint;
        pendingFlowHint = null;
        return hint;
    }

    function setPendingHint(hint) {
        pendingFlowHint = hint;
    }

    function resolveFlowHint(userText) {
        return consumePendingHint() || getFlowHintForTurn(userText);
    }

    function shouldAttachBlueprintButtons(content) {
        if (state.turn === 'done') return false;
        if (hasTechnicalBlueprint(content)) return true;
        const trimmed = (content || '').trim();
        const looksLikePlan =
            trimmed.length >= MIN_FULL_PLAN_CHARS &&
            !isLikelyClosingReply(content) &&
            (state.turn === 'awaiting_full_plan' ||
                state.turn === 'full_plan' ||
                trimmed.toLowerCase().includes('download pdf') ||
                trimmed.toLowerCase().includes('action step') ||
                trimmed.toLowerCase().includes('communication script'));
        return looksLikePlan;
    }

    function blueprintFilename() {
        return `solve-ninja-blueprint-${new Date().toISOString().slice(0, 10)}.pdf`;
    }

    function getHtml2Canvas() {
        return global.html2canvas || null;
    }

    function getJsPDFConstructor() {
        return (global.jspdf && global.jspdf.jsPDF) || global.jsPDF || null;
    }

    async function downloadBlueprintPdf(markdown, triggerBtn) {
        const clean = normalizeMarkdown(markdown);
        if (!clean || clean.length < 40) {
            alert('Your action blueprint is not ready yet. Ask for the full plan first.');
            return;
        }
        if (triggerBtn) triggerBtn.disabled = true;

        const host = document.createElement('div');
        host.id = 'pdf-capture-host';
        host.setAttribute('aria-hidden', 'true');
        const sheet = document.createElement('div');
        sheet.className = 'pdf-export-sheet';
        const bodyHtml =
            typeof marked !== 'undefined' ? marked.parse(clean) : clean.split('\n').map((l) => `<p>${l}</p>`).join('');
        sheet.innerHTML = `<h1>Action Blueprint</h1><div class="pdf-meta">Citizen Data Assistant · ${new Date().toLocaleDateString('en-IN')}</div><div class="pdf-body">${bodyHtml}</div>`;
        host.appendChild(sheet);
        document.body.appendChild(host);

        try {
            await new Promise((r) => setTimeout(r, 300));
            if (getHtml2Canvas() && getJsPDFConstructor()) {
                const canvas = await getHtml2Canvas()(sheet, { scale: 2, backgroundColor: '#ffffff' });
                const jsPDF = getJsPDFConstructor();
                const pdf = new jsPDF({ unit: 'mm', format: 'a4' });
                const margin = 10;
                const pageWidth = pdf.internal.pageSize.getWidth();
                const pageHeight = pdf.internal.pageSize.getHeight();
                const imgWidth = pageWidth - margin * 2;
                const imgHeight = (canvas.height * imgWidth) / canvas.width;
                const imgData = canvas.toDataURL('image/jpeg', 0.92);
                let heightLeft = imgHeight;
                let y = margin;
                pdf.addImage(imgData, 'JPEG', margin, y, imgWidth, imgHeight);
                heightLeft -= pageHeight - margin * 2;
                while (heightLeft > 0) {
                    y = margin - (imgHeight - heightLeft);
                    pdf.addPage();
                    pdf.addImage(imgData, 'JPEG', margin, y, imgWidth, imgHeight);
                    heightLeft -= pageHeight - margin * 2;
                }
                pdf.save(blueprintFilename());
            } else {
                const jsPDF = getJsPDFConstructor();
                if (!jsPDF) throw new Error('jsPDF missing');
                const doc = new jsPDF();
                doc.text(clean.slice(0, 8000), 10, 10, { maxWidth: 190 });
                doc.save(blueprintFilename());
            }
        } catch (err) {
            console.error(err);
            alert('Could not create PDF. Please try again.');
        } finally {
            host.remove();
            if (triggerBtn) triggerBtn.disabled = false;
        }
    }

    function addBlueprintButtons(container, pdfMarkdown, drawerMarkdown) {
        const actions = document.createElement('div');
        actions.className = 'bubble-actions';

        const viewBtn = document.createElement('button');
        viewBtn.type = 'button';
        viewBtn.className = 'bubble-btn bubble-btn-view';
        viewBtn.innerHTML = `${VIEW_ICON} View My Action Blueprint`;
        viewBtn.onclick = () => {
            currentBlueprintMarkdown = drawerMarkdown;
            if (onToggleDrawer) onToggleDrawer(true, drawerMarkdown);
        };
        actions.appendChild(viewBtn);

        const pdfBtn = document.createElement('button');
        pdfBtn.type = 'button';
        pdfBtn.className = 'bubble-btn bubble-btn-pdf';
        pdfBtn.innerHTML = `${PDF_ICON} Download PDF`;
        pdfBtn.onclick = () => downloadBlueprintPdf(pdfMarkdown, pdfBtn);
        actions.appendChild(pdfBtn);

        container.appendChild(actions);
    }

    function attachBlueprintUi(div, content) {
        const parts = content.split('---');
        const fullMd =
            parts.map((p) => p.trim()).filter(Boolean).join('\n\n---\n\n') || parts[0].trim();
        if (!fullMd) return;
        currentBlueprintMarkdown = fullMd;
        addBlueprintButtons(div, fullMd, fullMd);
        state.turn = 'full_plan';
        saveState();
    }

    /**
     * Render assistant bubble for solutions mode (recipe / blueprint UX).
     */
    function renderAssistantBubble(div, content) {
        const parts = content.split('---');
        div.innerHTML = renderMarkdown(parts[0], true);

        const technicalMd = parts.length > 1 ? parts.slice(1).join('---').trim() : '';
        if (technicalMd.length > 0) {
            const toggleBtn = document.createElement('button');
            toggleBtn.type = 'button';
            toggleBtn.className = 'blueprint-toggle';
            toggleBtn.textContent = '📋 Show step-by-step plan (scripts & officials)';
            const techWrap = document.createElement('div');
            techWrap.className = 'blueprint-technical collapsed';
            techWrap.innerHTML = renderMarkdown('---\n' + technicalMd, false);
            toggleBtn.onclick = () => {
                const collapsed = techWrap.classList.toggle('collapsed');
                toggleBtn.textContent = collapsed
                    ? '📋 Show step-by-step plan (scripts & officials)'
                    : '📋 Hide step-by-step plan';
            };
            div.appendChild(toggleBtn);
            div.appendChild(techWrap);
        }

        if (shouldAttachBlueprintButtons(content)) {
            attachBlueprintUi(div, content);
        }
    }

    function updateStateAfterMessage(content, userMsg) {
        if (state.turn === 'awaiting_first_win') {
            state.turn = 'first_win';
            saveState();
        }
        if (
            state.turn === 'awaiting_full_plan' &&
            (hasTechnicalBlueprint(content) || content.trim().length >= MIN_FULL_PLAN_CHARS)
        ) {
            state.turn = 'full_plan';
            saveState();
        }

        if (
            userMsg &&
            isMissionExploreCommit(userMsg.content) &&
            state.turn !== 'full_plan' &&
            state.turn !== 'done'
        ) {
            state.turn = 'first_win';
            saveState();
        }
    }

    function getChipsForMessage(content) {
        if (state.turn === 'done') return null;

        if (state.turn === 'first_win') {
            return { items: FULL_PLAN_CHIPS, header: 'What would you like next?' };
        }

        if (state.turn === 'picker') {
            if (isMissionPickerPrompt(content) || hasTwoMissionList(content)) {
                state.lastOfferedSingleMission = false;
                saveState();
                return { items: MISSION_PICK_CHIPS, header: 'Pick a mission:' };
            }
            if (isSingleMissionOffer(content)) {
                state.lastOfferedSingleMission = true;
                saveState();
                return { items: EXPLORE_CHIPS, header: 'Ready to start?' };
            }
        }

        if (state.turn === 'full_plan') {
            return { items: FULL_PLAN_CHIPS, header: 'Need anything else?' };
        }

        return null;
    }

    function ensureBlueprintButtons(content) {
        if (!shouldAttachBlueprintButtons(content)) return;
        const bubbles = document.querySelectorAll('#chat-window .assistant-bubble');
        const last = bubbles[bubbles.length - 1];
        if (last && !last.querySelector('.bubble-actions')) {
            attachBlueprintUi(last, content);
        }
    }

    function afterAssistantMessage(content, userMsg) {
        updateStateAfterMessage(content, userMsg);
        ensureBlueprintButtons(content);
        const chips = getChipsForMessage(content);
        if (chips && onRenderChips) onRenderChips(chips.items, chips.header);
    }

    function resumeUi(renderChipsFn, themes) {
        const labels = {
            picker: "Welcome back! What kind of issue are you facing? Pick a category or describe it in your own words.",
            first_win: 'Continue: try your **first-win step**, or ask for the **full plan + PDF**.',
            full_plan: 'Continue: download your blueprint or describe a new problem.',
            done: 'Welcome back! Describe a new problem whenever you are ready.',
        };
        return {
            message: labels[state.turn] || labels.picker,
            chips:
                state.turn === 'picker'
                    ? { items: themes, header: 'Explore Solution Categories:' }
                    : state.turn === 'first_win'
                      ? { items: FULL_PLAN_CHIPS, header: 'What would you like next?' }
                      : null,
        };
    }

    function getCurrentBlueprintMarkdown() {
        return currentBlueprintMarkdown;
    }

    global.CDASolutions = {
        activate,
        resetState,
        configure,
        resolveFlowHint,
        setPendingHint,
        beginMissionFirstWin,
        isMissionPick,
        isMissionExploreCommit,
        renderAssistantBubble,
        afterAssistantMessage,
        resumeUi,
        recipeStepIndex,
        updateRecipeBar,
        downloadBlueprintPdf,
        getCurrentBlueprintMarkdown,
        getState: () => ({ ...state }),
    };
})(window);
