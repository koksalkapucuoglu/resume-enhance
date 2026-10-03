/*
 * PDF import with a progress bar.
 *
 * The server streams where the parser is (reading → contact → experience →
 * education → projects → checking → saved) and how much it has written; this
 * draws that as a bar with a stage line, so a 30–40 second import reads as
 * work being done rather than a frozen spinner. Used by the start page and the
 * agentic chat (views.upload_cv, Accept: text/event-stream).
 *
 *   const result = await ResuImport.run(file, {url, csrf, labels, mount});
 *   // result: {ok, status, data}  — data is the same JSON as a plain import
 */
(function () {
    const STAGE_ICONS = {
        reading: 'description', contact: 'badge', skills: 'psychology',
        experience: 'work_history', education: 'school', projects: 'rocket_launch',
        checking: 'fact_check', saved: 'check_circle',
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function buildCard(file, labels) {
        const card = el('div', 'resu-import w-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm');
        const head = el('div', 'flex items-center gap-2 mb-3');
        const icon = el('span', 'material-symbols-outlined text-primary !text-[20px]', STAGE_ICONS.reading);
        const title = el('p', 'text-sm font-semibold text-slate-800 truncate flex-1', file.name);
        const pct = el('span', 'text-xs font-bold text-slate-500 tabular-nums', '0%');
        head.append(icon, title, pct);

        const track = el('div', 'w-full h-2 rounded-full bg-slate-100 overflow-hidden');
        const bar = el('div', 'h-2 rounded-full bg-primary');
        bar.style.width = '2%';
        bar.style.transition = 'width 0.6s ease';
        track.append(bar);

        const stage = el('p', 'text-sm text-slate-700 mt-3', labels.reading);
        const foot = el('div', 'flex items-center justify-between mt-1 text-xs text-slate-400');
        const tip = el('span', 'truncate pr-3', labels.hint);
        const clock = el('span', 'tabular-nums shrink-0', '0 s');
        foot.append(tip, clock);

        card.append(head, track, stage, foot);
        return {card, icon, pct, bar, stage, tip, clock};
    }

    function parseFrames(buffer) {
        const frames = [];
        let index;
        while ((index = buffer.indexOf('\n\n')) !== -1) {
            const raw = buffer.slice(0, index);
            buffer = buffer.slice(index + 2);
            const event = (raw.match(/^event: (.*)$/m) || [])[1];
            const data = (raw.match(/^data: (.*)$/m) || [])[1];
            if (event && data) {
                try { frames.push({event, data: JSON.parse(data)}); } catch (e) { /* skip */ }
            }
        }
        return {frames, rest: buffer};
    }

    async function run(file, opts) {
        const labels = opts.labels || {};
        const ui = buildCard(file, labels);
        if (opts.mount) opts.mount(ui.card);

        const started = Date.now();
        let shown = 2;
        let target = 5;
        const tips = (labels.tips || []).slice();
        let tipIndex = 0;
        // The bar never jumps or freezes: it eases towards the last reported
        // value and creeps a little while a long stage is still being written.
        const timer = setInterval(() => {
            const seconds = Math.round((Date.now() - started) / 1000);
            ui.clock.textContent = `${seconds} s`;
            if (shown < target) shown = Math.min(target, shown + Math.max(0.4, (target - shown) / 6));
            else if (shown < 95) shown = Math.min(95, shown + 0.05);
            ui.bar.style.width = `${shown}%`;
            ui.pct.textContent = `${Math.floor(shown)}%`;
            if (tips.length && seconds > 0 && seconds % 7 === 0) {
                ui.tip.textContent = tips[tipIndex++ % tips.length];
            }
        }, 300);

        const setStage = (name) => {
            if (labels[name]) ui.stage.textContent = labels[name];
            ui.icon.textContent = STAGE_ICONS[name] || 'description';
        };
        const finish = (ok, message) => {
            clearInterval(timer);
            if (ok) {
                ui.bar.style.width = '100%';
                ui.pct.textContent = '100%';
                setStage('saved');
            } else {
                ui.bar.classList.remove('bg-primary');
                ui.bar.classList.add('bg-red-400');
                ui.icon.textContent = 'error';
                ui.icon.classList.replace('text-primary', 'text-red-500');
                ui.stage.textContent = message || labels.failed || 'Import failed.';
                ui.stage.classList.add('text-red-600');
                ui.tip.textContent = '';
            }
        };

        const form = new FormData();
        form.append('cv_file', file);
        let response;
        try {
            response = await fetch(opts.url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': opts.csrf,
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'text/event-stream',
                },
                body: form,
            });
        } catch (e) {
            finish(false, labels.failed);
            return {ok: false, status: 0, data: {error: labels.failed}, card: ui.card};
        }

        // Refusals (limits, unverified email, not a PDF) come back as plain JSON.
        const type = response.headers.get('Content-Type') || '';
        if (!type.includes('text/event-stream')) {
            let data = {};
            try { data = await response.json(); } catch (e) { data = {error: labels.failed}; }
            finish(response.ok, data.error);
            return {ok: response.ok, status: response.status, data, card: ui.card};
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let result = null;
        try {
            while (true) {
                const {value, done} = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, {stream: true});
                const parsed = parseFrames(buffer);
                buffer = parsed.rest;
                for (const frame of parsed.frames) {
                    if (frame.event === 'progress') {
                        target = Math.max(target, frame.data.progress || 0);
                        setStage(frame.data.stage);
                        if (frame.data.linkedin && labels.linkedin) ui.tip.textContent = labels.linkedin;
                    } else if (frame.event === 'done') {
                        result = frame.data;
                    }
                }
            }
        } catch (e) {
            result = null;
        }

        if (!result) {
            finish(false, labels.failed);
            return {ok: false, status: 0, data: {error: labels.failed}, card: ui.card};
        }
        const status = result.http_status || 200;
        const ok = status === 200;
        finish(ok, result.error);
        return {ok, status, data: result, card: ui.card};
    }

    window.ResuImport = {run};
})();
