(() => {
    let serverUrl = localStorage.getItem("server_url") || "";
    let API = serverUrl ? serverUrl.replace(/\/+$/, "") : "";
    let token = localStorage.getItem("token");
    let currentUser = null;
    let chats = [];
    let currentChatId = null;
    let ws = null;
    let selectedUsers = new Set();
    let typingTimeout = null;
    let typingSendTimeout = null;
    let isMobile = window.innerWidth <= 768;
    let chatInterval = null;
    let replyToId = null;
    let editingMsgId = null;
    let contextMsgId = null;
    let forwardMsgId = null;
    let oldestMsgId = null;
    let loadingOlder = false;
    let notifPermission = false;
    let audioCtx = null;
    const decryptedMsgCache = {};
    const MAX_CACHE_SIZE = 500;

    const $ = s => document.querySelector(s);
    const $$ = s => document.querySelectorAll(s);

    function pruneCache() {
        if (Object.keys(decryptedMsgCache).length > MAX_CACHE_SIZE) {
            const keys = Object.keys(decryptedMsgCache);
            for (let i = 0; i < keys.length / 2; i++) {
                delete decryptedMsgCache[keys[i]];
            }
        }
    }

    const EMOJI_DATA = {
        "Частые": ["😀","😂","😍","🥰","😎","🤔","👍","👎","❤️","🔥","🎉","😢","😡","🥳","💀","👀","✅","❌","⭐","💡","🙏","💪","🤝","😍"],
        "Смайлики": ["😀","😃","😄","😁","😆","😅","🤣","😂","🙂","😊","😇","🥰","😍","🤩","😘","😗","😋","😛","😜","🤪","😝","🤑","🤗","🤭","🤫","🤔","🤐","🤨","😐","😑","😶","😏","😒","🙄","😬","🤥","😌","😔","😪","🤤","😴","😷","🤒","🤕","🤢","🤮","🥵","🥶","🥴","😵","🤯","🤠","🥳","😎","🤓","🧐"],
        "Жесты": ["👋","🤚","✋","🖖","👌","🤏","✌️","🤞","🤟","🤘","🤙","👈","👉","👆","👇","☝️","👍","👎","✊","👊","🤛","🤜","👏","🙌","👐","🤲","🤝","🙏"],
        "Животные": ["🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯","🦁","🐮","🐷","🐸","🐵","🙈","🙉","🙊","🐒","🐔","🐧","🐦","🐤","🦆","🦅","🦉","🦇","🐺","🐗","🐴","🦄","🐝","🐛","🦋","🐌","🐞","🐜"],
        "Еда": ["🍎","🍐","🍊","🍋","🍌","🍉","🍇","🍓","🫐","🍈","🍒","🍑","🥭","🍍","🥝","🍅","🥑","🍆","🥔","🥕","🌽","🌶️","🥒","🥬","🥦","🧄","🧅","🍄","🥜","🌰","🍞","🥐","🥖","🫓","🥨","🥯","🥞","🧇","🧀","🍖","🍗","🥩","🥓","🍔","🍟","🍕","🌭","🥪","🌮","🌯"],
        "Объекты": ["⌚","📱","💻","⌨️","🖥️","🖨️","🖱️","🖲️","💾","💿","📀","📷","📸","📹","🎥","📽️","📺","📻","🎙️","🎚️","🎛️","🧭","⏱️","⏰","🔋","🔌","💡","🔦","🕯️","🪙","💰","💵","💳","📦","📫","📬","📭","📮","🗳️","✏️","✒️","🖋️","🖊️","🖌️","📝","📁","📂","📅","📌","📎","🔒","🔓"],
        "Символы": ["❤️","🧡","💛","💚","💙","💜","🖤","🤍","🤎","💔","❣️","💕","💞","💓","💗","💖","💘","💝","⭐","🌟","💫","✨","🔥","💥","❄️","🌈","☀️","🌤️","⛅","🌥️","☁️","🌧️","⛈️","🌩️","🌪️","🌫️","🌍","🌎","🌏","🎯","🏆","🎮","🎲","🎭","🎨","🎬","🎤","🎧","🎵","🎶","🎹","🥁","🎷","🎺","🎸","🎻"]
    };

    function showChatView() {
        if (isMobile) { $("#sidebar").classList.add("hidden"); $("#chat-view").classList.remove("hidden"); }
    }
    function showSidebar() {
        if (isMobile) { $("#chat-view").classList.add("hidden"); $("#sidebar").classList.remove("hidden"); }
    }

    function toast(msg, type = "info") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = msg;
        $("#toast-container").appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }

    function getInitials(name) { return (name || "?").split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2); }
    function randomColor(s) {
        const c = ["#6c5ce7","#00b894","#e17055","#0984e3","#d63031","#e84393","#00cec9","#fdcb6e","#a29bfe","#ff7675"];
        let h = 0; for (let i = 0; i < (s || "").length; i++) h = s.charCodeAt(i) + ((h << 5) - h);
        return c[Math.abs(h) % c.length];
    }
    function avatarHtml(user) {
        if (user?.avatar_url) return `<img src="${esc(user.avatar_url)}" alt="">`;
        return getInitials(user?.display_name || user?.name);
    }
    function esc(t) { return (t || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
    function escAttr(t) { return (t || "").replace(/"/g, "&quot;"); }

    function formatTime(iso) {
        if (!iso) return "";
        const d = new Date(iso), now = new Date();
        const hh = String(d.getHours()).padStart(2, "0"), mm = String(d.getMinutes()).padStart(2, "0");
        if (d.toDateString() === now.toDateString()) return `${hh}:${mm}`;
        return `${String(d.getDate()).padStart(2,"0")}.${String(d.getMonth()+1).padStart(2,"0")} ${hh}:${mm}`;
    }
    function formatUptime(s) { const h = Math.floor(s/3600), m = Math.floor((s%3600)/60); return h > 0 ? `${h}ч ${m}м` : `${m}м`; }
    function getFileIcon(ext) {
        const i = {pdf:"\u{1F4C4}",doc:"\u{1F4DD}",docx:"\u{1F4DD}",txt:"\u{1F4DD}",jpg:"\u{1F5BC}",jpeg:"\u{1F5BC}",png:"\u{1F5BC}",gif:"\u{1F5BC}",webp:"\u{1F5BC}",mp3:"\u{1F3B5}",wav:"\u{1F3B5}",mp4:"\u{1F3AC}",avi:"\u{1F3AC}",mkv:"\u{1F3AC}",zip:"\u{1F4E6}",rar:"\u{1F4E6}","7z":"\u{1F4E6}",py:"\u{1F40D}",js:"\u{1F4DC}",html:"\u{1F310}",css:"\u{1F3A8}",exe:"\u{2699}",apk:"\u{1F4F1}"};
        return i[ext] || "\u{1F4C1}";
    }

    async function api(path, opts = {}) {
        const h = { ...opts.headers };
        if (token) h["Authorization"] = `Bearer ${token}`;
        if (opts.body && !(opts.body instanceof FormData)) { h["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.body); }
        const r = await fetch(`${API}${path}`, { ...opts, headers: h });
        if (!r.ok) { const e = await r.json().catch(() => ({ detail: "Error" })); throw new Error(e.detail || "Error"); }
        return r.json();
    }

    // Notifications
    function requestNotifPermission() {
        if (!("Notification" in window)) return;
        if (Notification.permission === "default") Notification.requestPermission().then(p => { notifPermission = p === "granted"; });
        else notifPermission = Notification.permission === "granted";
    }
    function showNotification(title, body) {
        if (!notifPermission || document.hasFocus()) return;
        try { new Notification(title, { body, icon: "/static/favicon.svg" }); } catch {}
    }
    function playNotifSound() {
        try {
            if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const o = audioCtx.createOscillator(), g = audioCtx.createGain();
            o.connect(g); g.connect(audioCtx.destination);
            o.type = "sine"; o.frequency.setValueAtTime(880, audioCtx.currentTime);
            o.frequency.exponentialRampToValueAtTime(440, audioCtx.currentTime + 0.15);
            g.gain.setValueAtTime(0.15, audioCtx.currentTime);
            g.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.25);
            o.start(); o.stop(audioCtx.currentTime + 0.25);
        } catch {}
    }

    // Decryption
    async function decryptMsg(msg) {
        if (msg._decrypted) return msg;
        if (!msg.encrypted_key && !msg.sender_encrypted_key) return msg;
        if (msg.message_type !== "text" || msg.is_deleted) return msg;
        const parts = (msg.content || "").split(":");
        if (parts.length < 2) return msg;
        const iv = parts[0], enc = parts.slice(1).join(":");
        if (!iv || !enc) return msg;
        const isOwn = String(msg.sender_id) === String(currentUser.id);
        const keys = isOwn
            ? [msg.sender_encrypted_key, msg.encrypted_key].filter(Boolean)
            : [msg.encrypted_key, msg.sender_encrypted_key].filter(Boolean);
        for (const key of keys) {
            try { return { ...msg, content: await CryptoManager.decryptMessage(enc, key, iv), _decrypted: true }; } catch {}
        }
        pruneCache();
        return { ...msg, content: "\u{1F512} [Не удалось расшифровать]", _decrypted: true };
    }

    // Theme
    function initTheme() {
        const saved = localStorage.getItem("theme") || "dark";
        if (saved === "light") { document.body.classList.add("light"); $("#theme-toggle-btn").innerHTML = "&#9790;"; }
        else { document.body.classList.remove("light"); $("#theme-toggle-btn").innerHTML = "&#9788;"; }
    }
    $("#theme-toggle-btn").addEventListener("click", () => {
        const isLight = document.body.classList.toggle("light");
        localStorage.setItem("theme", isLight ? "light" : "dark");
        $("#theme-toggle-btn").innerHTML = isLight ? "&#9790;" : "&#9788;";
    });
    initTheme();

    // Auth tabs
    $$(".auth-tab").forEach(tab => tab.addEventListener("click", () => {
        $$(".auth-tab").forEach(t => t.classList.remove("active"));
        tab.classList.add("active");
        $$(".auth-form").forEach(f => f.classList.remove("active"));
        $(tab.dataset.tab === "login" ? "#login-form" : "#register-form").classList.add("active");
    }));

    // Server toggle
    $("#server-toggle-btn").addEventListener("click", () => $("#server-settings-panel").classList.toggle("active"));
    $("#server-url-input").value = serverUrl;
    $("#server-url-input").addEventListener("change", () => {
        serverUrl = $("#server-url-input").value.trim().replace(/\/+$/, "");
        API = serverUrl;
        serverUrl ? localStorage.setItem("server_url", serverUrl) : localStorage.removeItem("server_url");
    });

    // Login
    $("#login-form").addEventListener("submit", async e => {
        e.preventDefault(); $("#login-error").textContent = "";
        try { const d = await api("/api/login", { method: "POST", body: { username: $("#login-username").value.trim(), password: $("#login-password").value } }); onAuth(d); }
        catch (err) { $("#login-error").textContent = err.message; }
    });

    // Register
    $("#register-form").addEventListener("submit", async e => {
        e.preventDefault(); $("#reg-error").textContent = "";
        const u = $("#reg-username").value.trim(), dn = $("#reg-displayname").value.trim(), pw = $("#reg-password").value;
        if (!u || !dn || !pw) { $("#reg-error").textContent = "Заполните все поля"; return; }
        try {
            const kp = await CryptoManager.generateKeyPair();
            localStorage.setItem("private_key", kp.privateKey);
            localStorage.setItem("public_key", kp.publicKey);
            const d = await api("/api/register", { method: "POST", body: { username: u, display_name: dn, password: pw, public_key: kp.publicKey } });
            onAuth(d);
        } catch (err) { $("#reg-error").textContent = err.message; }
    });

    function onAuth(data) {
        token = data.token; currentUser = data.user;
        localStorage.setItem("token", token);
        localStorage.setItem("user", JSON.stringify(currentUser));
        localStorage.setItem("user_id", String(currentUser.id));
        requestNotifPermission();
        const storedPub = localStorage.getItem("public_key");
        if (!localStorage.getItem("private_key")) {
            CryptoManager.generateKeyPair().then(kp => { localStorage.setItem("private_key", kp.privateKey); localStorage.setItem("public_key", kp.publicKey); updatePubKey(kp.publicKey); });
        } else if (storedPub && storedPub !== currentUser.public_key) {
            updatePubKey(storedPub);
        }
        startApp();
    }

    async function updatePubKey(pk) { try { await api("/api/me", { method: "PUT", body: { public_key: pk } }); } catch {} }

    async function startApp() {
        if (!currentUser) { try { currentUser = await api("/api/me"); } catch { logout(); return; } }
        $("#auth-screen").style.display = "none";
        $("#app-screen").classList.remove("hidden");
        $("#my-name").textContent = currentUser.display_name;
        updateMyAvatar();
        $("#sidebar").classList.remove("hidden");
        
        // Show admin button for kremp
        if (currentUser.username === "kremp") {
            $("#admin-btn").classList.remove("hidden");
        }
        
        // Fetch and display version
        try {
            const v = await api("/api/version");
            document.querySelectorAll(".version-static").forEach(el => el.textContent = "v" + v.version);
        } catch {}
        
        connectWebSocket();
        await loadChats();
        if (chatInterval) clearInterval(chatInterval);
        chatInterval = setInterval(loadChats, 8000);
    }

    function updateMyAvatar() {
        const av = $("#my-avatar");
        if (currentUser.avatar_url) { av.innerHTML = avatarHtml(currentUser); av.style.background = "transparent"; }
        else { av.textContent = getInitials(currentUser.display_name); av.style.background = randomColor(currentUser.display_name); }
    }

    function logout() {
        token = null; currentUser = null; currentChatId = null;
        localStorage.removeItem("token"); localStorage.removeItem("user"); localStorage.removeItem("user_id");
        localStorage.removeItem("private_key"); localStorage.removeItem("public_key");
        if (ws) ws.close(); if (chatInterval) clearInterval(chatInterval);
        $("#chat-area").classList.add("hidden");
        $("#chat-view").classList.add("hidden");
        $("#empty-state").style.display = "";
        $("#app-screen").classList.add("hidden");
        $("#auth-screen").style.display = "flex";
        $$(".auth-form").forEach(f => f.classList.remove("active"));
        $("#login-form").classList.add("active");
        $$(".auth-tab").forEach(t => t.classList.remove("active"));
        $$(".auth-tab")[0].classList.add("active");
    }
    $("#logout-btn").addEventListener("click", logout);

    // Avatar
    $("#avatar-edit-btn").addEventListener("click", e => { e.stopPropagation(); $("#avatar-input").click(); });
    $("#avatar-input").addEventListener("change", async () => {
        const f = $("#avatar-input").files[0]; if (!f) return; $("#avatar-input").value = "";
        if (f.size > 5*1024*1024) { toast("Макс 5 МБ", "error"); return; }
        const fd = new FormData(); fd.append("file", f);
        try { const d = await api("/api/me/avatar", { method: "POST", body: fd }); currentUser = d; localStorage.setItem("user", JSON.stringify(d)); updateMyAvatar(); toast("Аватар обновлён", "success"); } catch { toast("Ошибка", "error"); }
    });

    // Profile
    $("#profile-btn").addEventListener("click", () => {
        $("#profile-displayname").value = currentUser.display_name;
        $("#profile-bio").value = currentUser.bio || "";
        $("#profile-password").value = "";
        const av = $("#profile-modal-avatar");
        if (currentUser.avatar_url) { av.innerHTML = avatarHtml(currentUser); av.style.background = "transparent"; }
        else { av.textContent = getInitials(currentUser.display_name); av.style.background = randomColor(currentUser.display_name); }
        openModal("#modal-profile");
    });
    $("#profile-avatar-upload-btn").addEventListener("click", () => $("#profile-avatar-file-input").click());
    $("#profile-avatar-file-input").addEventListener("change", async () => {
        const f = $("#profile-avatar-file-input").files[0]; if (!f) return;
        const fd = new FormData(); fd.append("file", f);
        try { const d = await api("/api/me/avatar", { method: "POST", body: fd }); currentUser = d; localStorage.setItem("user", JSON.stringify(d)); updateMyAvatar(); toast("Аватар обновлён", "success"); } catch { toast("Ошибка", "error"); }
    });
    $("#profile-save").addEventListener("click", async () => {
        const dn = $("#profile-displayname").value.trim(), pw = $("#profile-password").value, bio = $("#profile-bio").value;
        const err = $("#profile-error"); err.textContent = "";
        if (!dn) { err.textContent = "Имя не может быть пустым"; return; }
        try {
            const body = { display_name: dn, bio: bio };
            if (pw.length > 0) { if (pw.length < 6) { err.textContent = "Минимум 6 символов"; return; } body.password = pw; }
            const d = await api("/api/me", { method: "PUT", body });
            currentUser = d; localStorage.setItem("user", JSON.stringify(d));
            $("#my-name").textContent = currentUser.display_name; updateMyAvatar();
            closeModal(); toast("Профиль обновлён", "success");
        } catch (e) { err.textContent = e.message; }
    });

    // User Profile View
    async function showUserProfile(userId) {
        try {
            const u = await api(`/api/users/${userId}`);
            const av = $("#user-profile-avatar");
            if (u.avatar_url) { av.innerHTML = avatarHtml(u); av.style.background = "transparent"; }
            else { av.textContent = getInitials(u.display_name); av.style.background = randomColor(u.display_name); }
            $("#user-profile-name").textContent = u.display_name;
            $("#user-profile-username").textContent = "@" + u.username;
            $("#user-profile-online").textContent = u.online ? "\u25CF В сети" : "\u25CB Оффлайн";
            $("#user-profile-online").style.color = u.online ? "var(--success)" : "var(--text-muted)";
            $("#user-profile-bio").textContent = u.bio || "Нет описания";
            openModal("#modal-user-profile");
        } catch { toast("Ошибка загрузки профиля", "error"); }
    }

    // Host Info
    $("#host-info-btn").addEventListener("click", async () => {
        const c = $("#host-info-content"); c.innerHTML = '<div class="spinner"></div>';
        openModal("#modal-host-info");
        try {
            const info = await api("/api/host-info");
            const url = info.tunnel_url || "Не настроен";
            c.innerHTML = `
                <div class="form-group"><label>Ссылка</label><div style="color:var(--accent);word-break:break-all;cursor:pointer" onclick="navigator.clipboard.writeText('${esc(url)}');alert('Скопировано!')">${esc(url)}</div></div>
                <div class="form-group"><label>Тип</label><div>${info.permanent ? "Постоянный" : "Временный"}</div></div>
                <div class="form-group"><label>Работает</label><div>${formatUptime(info.uptime_seconds)}</div></div>
                <div class="form-group"><label>Онлайн</label><div>${info.online_users} чел.</div></div>
                <div class="form-group"><label>Поделиться</label><div style="font-size:13px;color:var(--text-secondary)">Отправьте ссылку другу. Он вставит её в поле "Сервер" на экране входа.</div></div>`;
        } catch { c.innerHTML = "<p>Ошибка</p>"; }
    });

    // Admin Panel
    $("#admin-btn").addEventListener("click", async () => {
        const c = $("#admin-content"); c.innerHTML = '<div class="spinner"></div>';
        openModal("#modal-admin");
        try {
            const [stats, users, chats] = await Promise.all([
                api("/api/admin/stats"),
                api("/api/admin/users"),
                api("/api/admin/chats")
            ]);
            c.innerHTML = `
                <div class="admin-section">
                    <h4>&#128202; Статистика</h4>
                    <div class="stats-grid">
                        <div class="stat-item"><span class="stat-val">${stats.users}</span><span class="stat-lbl">Пользователей</span></div>
                        <div class="stat-item"><span class="stat-val">${stats.chats}</span><span class="stat-lbl">Чатов</span></div>
                        <div class="stat-item"><span class="stat-val">${stats.messages}</span><span class="stat-lbl">Сообщений</span></div>
                        <div class="stat-item"><span class="stat-val">${stats.online}</span><span class="stat-lbl">Онлайн</span></div>
                    </div>
                </div>
                <div class="admin-section">
                    <h4>&#128100; Пользователи (${users.length})</h4>
                    <div class="admin-table">
                        <div class="admin-row header"><span>ID</span><span>Username</span><span>Name</span><span>Bio</span><span>Created</span><span>Action</span></div>
                        ${users.map(u => `
                            <div class="admin-row">
                                <span>${u.id}</span>
                                <span>${esc(u.username)}</span>
                                <span>${esc(u.display_name)}</span>
                                <span>${esc(u.bio || "-")}</span>
                                <span>${u.created_at ? new Date(u.created_at).toLocaleString() : "-"}</span>
                                <span><button class="btn-text" onclick="adminDeleteUser(${u.id})">&#128465;</button></span>
                            </div>
                        `).join("")}
                    </div>
                </div>
                <div class="admin-section">
                    <h4>&#128172; Чаты (${chats.length})</h4>
                    <div class="admin-table">
                        <div class="admin-row header"><span>ID</span><span>Name</span><span>Type</span><span>Members</span><span>Created</span><span>Action</span></div>
                        ${chats.map(c => `
                            <div class="admin-row">
                                <span>${c.id}</span>
                                <span>${esc(c.name || "-")}</span>
                                <span>${c.is_group ? "Группа" : "ЛС"}</span>
                                <span>${c.member_count}</span>
                                <span>${c.created_at ? new Date(c.created_at).toLocaleString() : "-"}</span>
                                <span><button class="btn-text" onclick="adminDeleteChat(${c.id})">&#128465;</button></span>
                            </div>
                        `).join("")}
                    </div>
                </div>
            `;
        } catch { c.innerHTML = "<p>Ошибка загрузки</p>"; }
    });

    // Chat Menu
    $("#chat-menu-btn").addEventListener("click", () => {
        if (!currentChatId) return;
        const chat = chats.find(c => String(c.id) === String(currentChatId));
        if (!chat) return;
        const isGroup = chat.is_group;
        $("#chat-avatar-group").classList.toggle("hidden", !isGroup);
        $("#chat-rename-group").classList.toggle("hidden", !isGroup);
        $("#chat-leave-btn").classList.toggle("hidden", !isGroup);
        if (isGroup) { $("#chat-rename-input").value = chat.name; }
        if (isGroup) {
            $("#chat-members-group").classList.remove("hidden");
            renderMembersList(chat);
            $("#chat-theme-group").classList.remove("hidden");
            const current = chat.theme_color || "";
            $$(".theme-color-btn").forEach(b => b.classList.toggle("active", b.dataset.color === current));
        } else {
            $("#chat-members-group").classList.add("hidden");
            $("#chat-theme-group").classList.add("hidden");
        }
        openModal("#modal-chat-menu");
    });
    $("#chat-avatar-upload-btn").addEventListener("click", () => $("#chat-avatar-file-input").click());
    $("#chat-avatar-file-input").addEventListener("change", async () => {
        const f = $("#chat-avatar-file-input").files[0]; if (!f || !currentChatId) return;
        const fd = new FormData(); fd.append("file", f);
        try { await api(`/api/chats/${currentChatId}/avatar`, { method: "POST", body: fd }); await loadChats(); toast("Аватар обновлён", "success"); } catch { toast("Ошибка", "error"); }
    });
    $("#chat-rename-btn").addEventListener("click", async () => {
        const name = $("#chat-rename-input").value.trim(); if (!name || !currentChatId) return;
        try { await api(`/api/chats/${currentChatId}`, { method: "PUT", body: { name } }); await loadChats(); toast("Переименовано", "success"); closeModal(); } catch { toast("Ошибка", "error"); }
    });
    $("#chat-leave-btn").addEventListener("click", async () => {
        if (!currentChatId || !confirm("Покинуть чат?")) return;
        try { await api(`/api/chats/${currentChatId}/leave`, { method: "POST" }); currentChatId = null; showSidebar(); await loadChats(); closeModal(); toast("Вы покинули чат", "info"); } catch { toast("Ошибка", "error"); }
    });
    $("#chat-delete-btn").addEventListener("click", async () => {
        if (!currentChatId || !confirm("Удалить чат?")) return;
        const id = currentChatId; currentChatId = null;
        $("#chat-view").classList.add("hidden"); $("#empty-state").style.display = ""; showSidebar();
        try { await api(`/api/chats/${id}`, { method: "DELETE" }); await loadChats(); closeModal(); toast("Чат удалён", "info"); } catch { await loadChats(); }
    });

    // Members list
    async function renderMembersList(chat) {
        const list = $("#members-list"); list.innerHTML = "";
        if (!chat.members) return;
        const myRole = chat.members.find(m => m.id === currentUser.id)?.role;
        for (const m of chat.members) {
            const item = document.createElement("div");
            item.className = "member-item";
            const isOwner = m.role === "owner";
            const isAdmin = m.role === "admin";
            item.innerHTML = `
                <div class="member-info">
                    <div class="chat-avatar" style="width:32px;height:32px;font-size:12px;${m.avatar_url ? "" : "background:" + randomColor(m.display_name)}">${avatarHtml(m)}</div>
                    <div><div class="member-name">${esc(m.display_name)} ${isOwner ? '<span class="role-badge owner">Владелец</span>' : isAdmin ? '<span class="role-badge admin">Админ</span>' : ''}</div></div>
                </div>
                <div class="member-actions">
                    ${myRole === "owner" && !isOwner ? `<button class="btn-text" onclick="promoteMember(${m.id}, '${m.role === 'admin' ? 'member' : 'admin'}')">${m.role === 'admin' ? 'Понизить' : 'Повысить'}</button>` : ''}
                    ${myRole === "owner" && !isOwner ? `<button class="btn-text" style="color:var(--danger)" onclick="kickMember(${m.id})">Удалить</button>` : ''}
                </div>`;
            list.appendChild(item);
        }
    }
    window.kickMember = async function(userId) {
        if (!confirm("Удалить участника?")) return;
        try { await api(`/api/chats/${currentChatId}/members/${userId}`, { method: "DELETE" }); toast("Удалён", "info"); await loadChats(); } catch { toast("Ошибка", "error"); }
    };
    window.promoteMember = async function(userId, newRole) {
        try { await api(`/api/chats/${currentChatId}/members/${userId}`, { method: "PUT", body: { role: newRole } }); toast("Роль обновлена", "success"); await loadChats(); } catch { toast("Ошибка", "error"); }
    };

    window.adminDeleteUser = async function(userId) {
        if (!confirm("Удалить пользователя?")) return;
        try { await api(`/api/admin/users/${userId}`, { method: "DELETE" }); toast("Пользователь удалён", "success"); $("#admin-btn").click(); } catch { toast("Ошибка", "error"); }
    };
    window.adminDeleteChat = async function(chatId) {
        if (!confirm("Удалить чат?")) return;
        try { await api(`/api/admin/chats/${chatId}`, { method: "DELETE" }); toast("Чат удалён", "success"); $("#admin-btn").click(); } catch { toast("Ошибка", "error"); }
    };

    // Chat theme
    function applyChatTheme(color) {
        const cv = document.querySelector(".chat-view");
        if (cv) cv.style.setProperty("--chat-accent", color || "var(--accent)");
    }
    $$(".theme-color-btn").forEach(btn => btn.addEventListener("click", async () => {
        if (!currentChatId) return;
        const color = btn.dataset.color || null;
        try { await api(`/api/chats/${currentChatId}`, { method: "PUT", body: { theme_color: color } }); toast("Цвет обновлён", "success"); $$(".theme-color-btn").forEach(b => b.classList.remove("active")); btn.classList.add("active"); applyChatTheme(color); await loadChats(); } catch { toast("Ошибка", "error"); }
    }));

    // GIF picker
    let gifSearchTimeout;
    let tenorApiKey = "";

    $("#gif-btn")?.addEventListener("click", e => {
        e.stopPropagation();
        $("#sticker-picker").classList.add("hidden");
        $("#gif-picker").classList.toggle("hidden");
    });
    $("#gif-close")?.addEventListener("click", () => $("#gif-picker").classList.add("hidden"));
    $("#gif-search")?.addEventListener("input", () => {
        clearTimeout(gifSearchTimeout);
        gifSearchTimeout = setTimeout(async () => {
            const q = $("#gif-search").value.trim();
            if (!q) return;
            const apiKey = tenorApiKey || "LIVDSRZULELA"; // Demo key - replace with your own
            try {
                const r = await fetch(`https://tenor.googleapis.com/v2/search?q=${encodeURIComponent(q)}&key=${apiKey}&limit=20&media_filter=gif,tinygif`);
                const d = await r.json();
                const body = $("#gif-picker-body"); body.innerHTML = "";
                for (const result of d.results || []) {
                    const img = document.createElement("img");
                    img.className = "gif-item";
                    img.src = result.media_formats?.tinygif?.url || result.media_formats?.gif?.url;
                    img.loading = "lazy";
                    img.addEventListener("click", async () => {
                        if (!currentChatId) return;
                        const gifUrl = result.media_formats?.gif?.url;
                        try {
                            ws.send(JSON.stringify({
                                type: "message",
                                chat_id: currentChatId,
                                content: result.title || "GIF",
                                message_type: "file",
                                file_url: gifUrl,
                                file_name: "gif.gif"
                            }));
                            $("#gif-picker").classList.add("hidden");
                            toast("GIF отправлен", "success");
                        } catch { toast("Ошибка", "error"); }
                    });
                    body.appendChild(img);
                }
            } catch {}
        }, 400);
    });

    // Sticker picker
    const STICKER_PACKS = {
        "Коты": ["\u{1F63A}","\u{1F638}","\u{1F639}","\u{1F63B}","\u{1F63C}","\u{1F63D}","\u{1F640}","\u{1F63F}","\u{1F63E}","\u{1F431}","\u{1F408}","\u{1F408}\u200D\u{1F4BB}"],
        "Животные": ["\u{1F436}","\u{1F98A}","\u{1F43B}","\u{1F43C}","\u{1F428}","\u{1F42F}","\u{1F981}","\u{1F42E}","\u{1F437}","\u{1F438}","\u{1F435}","\u{1F430}"],
        "Еда": ["\u{1F355}","\u{1F354}","\u{1F35F}","\u{1F32E}","\u{1F32F}","\u{1F363}","\u{1F370}","\u{1F36A}","\u{1F36B}","\u{1F382}","\u2615","\u{1F37A}"],
        "Спорт": ["\u26BD","\u{1F3C0}","\u{1F3C8}","\u26BE","\u{1F3BE}","\u{1F3D0}","\u{1F3B1}","\u{1F3D3}","\u{1F94A}","\u{1F6B4}","\u{1F3C3}","\u{1F3C4}"],
        "Эмоции": ["\u{1F602}","\u{1F923}","\u{1F62D}","\u{1F631}","\u{1F914}","\u{1F60E}","\u{1F973}","\u{1F929}","\u{1F480}","\u{1F47B}","\u{1F383}","\u{1F47D}"]
    };
    function buildStickerPicker() {
        const body = $("#sticker-picker-body"); body.innerHTML = "";
        for (const [cat, stickers] of Object.entries(STICKER_PACKS)) {
            const catEl = document.createElement("div");
            catEl.className = "sticker-category"; catEl.textContent = cat;
            body.appendChild(catEl);
            for (const s of stickers) {
                const btn = document.createElement("button");
                btn.className = "sticker-item"; btn.textContent = s;
                btn.addEventListener("click", ev => {
                    ev.stopPropagation();
                    if (!currentChatId) return;
                    ws.send(JSON.stringify({ type: "message", chat_id: currentChatId, content: s, message_type: "sticker" }));
                    $("#sticker-picker").classList.add("hidden");
                });
                body.appendChild(btn);
            }
        }
    }
    $("#sticker-btn")?.addEventListener("click", e => {
        e.stopPropagation();
        $("#gif-picker").classList.add("hidden");
        buildStickerPicker();
        $("#sticker-picker").classList.toggle("hidden");
    });
    $("#sticker-close")?.addEventListener("click", () => $("#sticker-picker").classList.add("hidden"));

    // Modals
    function openModal(sel) { $(sel).classList.remove("hidden"); $("#modal-overlay").classList.remove("hidden"); }
    function closeModal() { $$(".modal, .modal-lightbox").forEach(m => m.classList.add("hidden")); $("#modal-overlay").classList.add("hidden"); }
    $$("[data-close-modal]").forEach(b => b.addEventListener("click", closeModal));
    $("#modal-overlay").addEventListener("click", e => { if (e.target === $("#modal-overlay")) closeModal(); });

    // Back
    $("#back-btn").addEventListener("click", () => {
        currentChatId = null;
        $("#chat-view").classList.add("hidden"); $("#empty-state").style.display = "";
        showSidebar(); renderChatList($("#search-input").value);
    });

    // Chat Search
    $("#search-chat-btn").addEventListener("click", () => { const bar = $("#chat-search-bar"); bar.classList.toggle("hidden"); if (!bar.classList.contains("hidden")) $("#chat-search-input").focus(); });
    $("#chat-search-close").addEventListener("click", () => { $("#chat-search-bar").classList.add("hidden"); $("#chat-search-input").value = ""; });
    let chatSearchTimeout;
    $("#chat-search-input").addEventListener("input", () => {
        clearTimeout(chatSearchTimeout);
        chatSearchTimeout = setTimeout(async () => {
            const q = $("#chat-search-input").value.trim(); if (!q || !currentChatId) return;
            try {
                const results = await api(`/api/chats/${currentChatId}/search?q=${encodeURIComponent(q)}`);
                if (results.length > 0) {
                    toast(`Найдено: ${results.length}`, "info");
                    const el = document.querySelector(`[data-msg-id="${results[0].id}"]`);
                    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.classList.add("msg-highlight"); setTimeout(() => el.classList.remove("msg-highlight"), 2000); }
                } else { toast("Ничего не найдено", "info"); }
            } catch {}
        }, 500);
    });

    // Scroll + Pagination
    $("#messages-container")?.addEventListener("scroll", () => {
        const c = $("#messages-container"); if (!c) return;
        const diff = c.scrollHeight - c.scrollTop - c.clientHeight;
        $("#scroll-bottom-btn").classList.toggle("hidden", diff < 200);
        if (c.scrollTop < 60 && !loadingOlder && oldestMsgId) loadOlderMessages();
    });
    $("#scroll-bottom-btn").addEventListener("click", () => { scrollToBottom(); $("#scroll-bottom-btn").classList.add("hidden"); });

    async function loadOlderMessages() {
        if (!currentChatId || loadingOlder) return;
        loadingOlder = true;
        const container = $("#messages-container");
        const prevHeight = container.scrollHeight;
        try {
            const msgs = await api(`/api/chats/${currentChatId}/messages?before_id=${oldestMsgId}&limit=30`);
            if (msgs.length === 0) { loadingOlder = false; return; }
            for (const m of msgs) await appendMessage(m, true);
            oldestMsgId = msgs[0].id;
            requestAnimationFrame(() => { container.scrollTop = container.scrollHeight - prevHeight; });
        } catch {} finally { loadingOlder = false; }
    }

    // WebSocket
    function connectWebSocket() {
        if (ws) ws.close();
        let host = location.host, proto = location.protocol === "https:" ? "wss:" : "ws:";
        if (serverUrl) { try { const u = new URL(serverUrl); host = u.host; proto = u.protocol === "https:" ? "wss:" : "ws:"; } catch {} }
        ws = new WebSocket(`${proto}//${host}/ws?token=${token}`);
        ws.onopen = () => { $("#connection-banner").classList.add("hidden"); };
        ws.onmessage = e => { try { handleWs(JSON.parse(e.data)); } catch {} };
        ws.onclose = () => { if (token) { $("#connection-banner").classList.remove("hidden"); setTimeout(connectWebSocket, 3000); } };
        ws.onerror = () => {};
    }

    function handleWs(data) {
        switch (data.type) {
            case "message": onNewMessage(data.message); break;
            case "typing": onTyping(data); break;
            case "read": break;
            case "read_receipt": onReadReceipt(data); break;
            case "chat_deleted": onChatDeleted(data.chat_id); break;
            case "profile_update": onProfileUpdate(data); break;
            case "presence": loadChats(); break;
            case "reaction": onReaction(data); break;
            case "message_edited": onMessageEdited(data); break;
            case "message_deleted": onMessageDeleted(data); break;
            case "chat_update": onChatUpdate(data); break;
        }
    }

    function onNewMessage(msg) {
        const isCurrent = String(msg.chat_id) === String(currentChatId);
        if (isCurrent) {
            appendMessage(msg, false);
            if (String(msg.sender_id) !== String(currentUser.id)) sendReadReceipt();
        }
        if (String(msg.sender_id) !== String(currentUser.id)) {
            playNotifSound();
            const chatName = isCurrent ? "" : (chats.find(c => String(c.id) === String(msg.chat_id))?.name || "Чат");
            showNotification(msg.sender_name || chatName, msg.content || "Файл");
        }
        loadChats();
    }
    function onTyping(d) {
        if (String(d.chat_id) === String(currentChatId)) {
            $("#typing-indicator").textContent = `${d.user_name} печатает...`;
            clearTimeout(typingTimeout);
            typingTimeout = setTimeout(() => { if ($("#typing-indicator")) $("#typing-indicator").textContent = ""; }, 3000);
        }
    }
    function onReadReceipt(d) {
        if (String(d.chat_id) !== String(currentChatId)) return;
        $$(".message-row.own").forEach(row => {
            const msgId = parseInt(row.dataset.msgId);
            if (msgId && msgId <= d.last_read_id) {
                const statusEl = row.querySelector(".msg-status");
                if (statusEl) statusEl.innerHTML = '<span class="status-read">\u2713\u2713</span>';
            }
        });
    }
    function onChatDeleted(id) {
        if (String(id) === String(currentChatId)) {
            currentChatId = null; $("#chat-view").classList.add("hidden");
            $("#empty-state").style.display = ""; showSidebar();
        }
        loadChats();
    }
    function onProfileUpdate(d) {
        const chat = chats.find(c => c.other_user?.id === d.user_id);
        if (chat?.other_user) { chat.other_user.display_name = d.display_name; chat.other_user.avatar_url = d.avatar_url; }
        if (String(d.user_id) === String(currentUser?.id)) { Object.assign(currentUser, d); localStorage.setItem("user", JSON.stringify(currentUser)); updateMyAvatar(); }
        loadChats();
    }
    function onReaction(d) { loadChats(); if (currentChatId && String(d.chat_id) === String(currentChatId)) reloadMessages(); }
    function onMessageEdited(d) { loadChats(); if (String(d.chat_id) === String(currentChatId)) reloadMessages(); }
    function onMessageDeleted(d) { loadChats(); if (String(d.chat_id) === String(currentChatId)) reloadMessages(); }
    function onChatUpdate(d) { loadChats(); }
    function sendReadReceipt() {
        if (currentChatId && ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "read", chat_id: currentChatId }));
            api(`/api/chats/${currentChatId}/read`, { method: "POST" }).catch(() => {});
        }
    }

    // Chats
    async function loadChats() {
        try { chats = await api("/api/chats"); renderChatList($("#search-input")?.value || ""); } catch {}
    }

    async function renderChatList(filter = "") {
        const list = $("#chat-list"); if (!list) return; list.innerHTML = "";
        const filtered = chats.filter(c => (c.name || "").toLowerCase().includes(filter.toLowerCase()));
        for (const chat of filtered) {
            const item = document.createElement("div");
            item.className = `chat-item${String(chat.id) === String(currentChatId) ? " active" : ""}`;
            const col = randomColor(chat.name);
            const isOnline = chat.other_user?.online;
            const last = chat.last_message;
            let preview = "";
            if (last) {
                if (last.is_deleted) preview = "[удалено]";
                else if (last.message_type === "sticker") preview = "\u{1F4AC} " + (last.content || "Стикер");
                else if (last.message_type === "file") preview = "\u{1F4CE} " + (last.file_name || "Файл");
                else if (last.encrypted_key || last.sender_encrypted_key) {
                    const parts = (last.content || "").split(":");
                    if (parts.length >= 2) {
                        const isOwn = String(last.sender_id) === String(currentUser.id);
                        const keys = isOwn ? [last.sender_encrypted_key, last.encrypted_key].filter(Boolean) : [last.encrypted_key, last.sender_encrypted_key].filter(Boolean);
                        for (const k of keys) { try { preview = await CryptoManager.decryptMessage(parts.slice(1).join(":"), k, parts[0]); break; } catch {} }
                    }
                    if (!preview) preview = "\u{1F512} зашифровано";
                } else preview = last.content || "";
            }
            const avUser = chat.other_user || { display_name: chat.name, avatar_url: chat.avatar_url };
            const hasAvatar = !!avUser.avatar_url;
            item.innerHTML = `
                <div class="chat-avatar" style="${hasAvatar ? "" : "background:" + col}">${avatarHtml(avUser)}${isOnline ? '<div class="online-dot"></div>' : ""}</div>
                <div class="chat-info"><div class="chat-name">${esc(chat.name)}</div><div class="chat-preview">${esc(preview)}</div></div>
                <div class="chat-meta"><div class="chat-time">${formatTime(last?.created_at)}</div>${chat.unread > 0 ? `<div class="unread-badge">${chat.unread > 99 ? "99+" : chat.unread}</div>` : ""}</div>`;
            item.addEventListener("click", () => openChat(chat));
            list.appendChild(item);
        }
    }
    $("#search-input")?.addEventListener("input", () => renderChatList($("#search-input").value));

    // Open Chat
    async function openChat(chat) {
        currentChatId = chat.id; oldestMsgId = null;
        $("#empty-state").style.display = "none";
        $("#chat-view").classList.remove("hidden");
        $("#chat-search-bar").classList.add("hidden"); $("#chat-search-input").value = "";
        $("#reply-preview").classList.add("hidden"); $("#edit-preview").classList.add("hidden");
        replyToId = null; editingMsgId = null;

        const av = $("#chat-avatar");
        const avUser = chat.other_user || { display_name: chat.name, avatar_url: chat.avatar_url };
        if (avUser?.avatar_url) { av.innerHTML = avatarHtml(avUser); av.style.background = "transparent"; }
        else { av.textContent = getInitials(chat.name); av.style.background = randomColor(chat.name); }
        $("#chat-header-name").textContent = chat.name;
        applyChatTheme(chat.theme_color);

        if (chat.other_user) {
            $("#chat-header-status").textContent = chat.other_user.online ? "в сети" : "оффлайн";
            $("#encryption-badge").className = "encryption-badge";
            $("#encryption-badge").innerHTML = "\u{1F512} E2E";
        } else {
            $("#chat-header-status").textContent = `${chat.members?.length || 0} участников`;
            $("#encryption-badge").className = "encryption-badge no-e2e";
            $("#encryption-badge").innerHTML = "\u{26A0} Группа";
        }

        renderChatList($("#search-input")?.value || "");
        showChatView();
        await reloadMessages();
        sendReadReceipt();
    }

    async function reloadMessages() {
        if (!currentChatId) return;
        const container = $("#messages-container");
        container.innerHTML = '<div class="spinner" style="margin:auto"></div>';
        try {
            const msgs = await api(`/api/chats/${currentChatId}/messages`);
            container.innerHTML = "";
            for (const m of msgs) await appendMessage(m, true);
            if (msgs.length > 0) oldestMsgId = msgs[0].id;
            scrollToBottom();
        } catch { container.innerHTML = '<div class="empty-state"><p>Ошибка загрузки</p></div>'; }
    }

    // Messages
    function getReadStatusHtml(msg, isOwn) {
        if (!isOwn || !msg.read_by) return "";
        const others = msg.read_by.filter(r => String(r.user_id) !== String(currentUser.id));
        if (others.length > 0) return '<span class="msg-status"><span class="status-read">\u2713\u2713</span></span>';
        return '<span class="msg-status"><span class="status-delivered">\u2713\u2713</span></span>';
    }

    async function appendMessage(msg, isHistory) {
        msg = await decryptMsg(msg);
        if (msg._decrypted) decryptedMsgCache[msg.id] = msg.content;
        const isOwn = String(msg.sender_id) === String(currentUser.id);
        const row = document.createElement("div");
        row.className = `message-row${isOwn ? " own" : ""}`;
        row.dataset.msgId = msg.id;

        let replyHtml = "";
        if (msg.reply_to_id && msg.reply_to_content) {
            let replyText = msg.reply_to_content;
            if (decryptedMsgCache[msg.reply_to_id]) {
                replyText = decryptedMsgCache[msg.reply_to_id];
            } else if (replyText.includes(":") && replyText.length > 30 && !replyText.includes(" ")) {
                replyText = "[зашифровано]";
            }
            replyHtml = `<div class="reply-indicator"><div class="reply-sender">${esc(msg.reply_to_sender || "")}</div><div class="reply-text">${esc(replyText)}</div></div>`;
        }

        let contentHtml = "";
        if (msg.is_deleted) {
            contentHtml = `<div class="message-text" style="color:var(--text-muted);font-style:italic">\u{1F5D1} [удалено]</div>`;
        } else if (msg.message_type === "sticker") {
            contentHtml = `<div class="message-text message-sticker">${esc(msg.content)}</div>`;
        } else if (msg.message_type === "file") {
            const fn = msg.file_name || "file", ext = fn.split(".").pop().toLowerCase();
            const isImg = ["jpg","jpeg","png","gif","webp"].includes(ext);
            if (isImg) {
                contentHtml = `<div class="message-text">${msg.content !== fn ? esc(msg.content) + "<br>" : ""}<a href="${escAttr(msg.file_url)}" target="_blank" class="lightbox-trigger"><img class="message-image" src="${escAttr(msg.file_url)}" alt="${escAttr(fn)}" loading="lazy"></a></div>`;
            } else {
                contentHtml = `<div class="message-text">${msg.content !== fn ? esc(msg.content) + "<br>" : ""}<a class="message-file" href="${escAttr(msg.file_url)}" target="_blank" download="${escAttr(fn)}"><span class="message-file-icon">${getFileIcon(ext)}</span><span>${esc(fn)}</span></a></div>`;
            }
        } else {
            contentHtml = `<div class="message-text">${esc(msg.content)}</div>`;
        }

        const editedTag = msg.is_edited && !msg.is_deleted ? '<span class="message-edited">(изменено)</span>' : "";
        const readStatus = getReadStatusHtml(msg, isOwn);

        let reactionsHtml = "";
        if (msg.reactions && msg.reactions.length > 0) {
            const grouped = {};
            msg.reactions.forEach(r => { grouped[r.emoji] = (grouped[r.emoji] || 0) + 1; });
            reactionsHtml = `<div class="reactions-row">${Object.entries(grouped).map(([e, c]) =>
                `<span class="reaction-badge" data-emoji="${escAttr(e)}">${e}${c > 1 ? " " + c : ""}</span>`
            ).join("")}</div>`;
        }

        row.innerHTML = `<div class="message-bubble">${!isOwn ? `<div class="message-sender" data-user-id="${msg.sender_id}">${esc(msg.sender_name)}</div>` : ""}${replyHtml}${contentHtml}<div class="message-time">${formatTime(msg.created_at)}${readStatus}</div>${reactionsHtml}</div>`;

        row.addEventListener("contextmenu", e => showContextMenu(e, msg));
        row.addEventListener("dblclick", e => { e.preventDefault(); showContextMenu(e, msg); });

        const senderEl = row.querySelector(".message-sender");
        if (senderEl) senderEl.addEventListener("click", () => showUserProfile(msg.sender_id));

        row.querySelectorAll(".lightbox-trigger").forEach(a => {
            a.addEventListener("click", e => { e.preventDefault(); showLightbox(a.querySelector("img").src); });
        });

        const container = $("#messages-container");
        if (isHistory) {
            container.appendChild(row);
        } else {
            const wasAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
            container.appendChild(row);
            if (wasAtBottom) scrollToBottom();
        }
    }

    function scrollToBottom() { requestAnimationFrame(() => { const c = $("#messages-container"); if (c) c.scrollTop = c.scrollHeight; }); }

    // Context Menu
    function showContextMenu(e, msg) {
        e.preventDefault(); e.stopPropagation();
        contextMsgId = msg.id;
        const isOwn = String(msg.sender_id) === String(currentUser.id);
        $("#ctx-edit").style.display = isOwn && !msg.is_deleted ? "flex" : "none";
        $("#ctx-delete").style.display = !msg.is_deleted ? "flex" : "none";
        $("#ctx-forward").style.display = !msg.is_deleted ? "flex" : "none";
        const menu = $("#context-menu");
        menu.classList.remove("hidden");
        menu.style.left = Math.min(e.clientX || e.pageX, window.innerWidth - 180) + "px";
        menu.style.top = Math.min(e.clientY || e.pageY, window.innerHeight - 180) + "px";
    }

    document.addEventListener("click", e => {
        if (!e.target.closest("#context-menu")) { $("#context-menu").classList.add("hidden"); }
        if (!e.target.closest("#reaction-picker") && !e.target.closest("#ctx-react")) { $("#reaction-picker").classList.add("hidden"); }
        if (!e.target.closest("#emoji-picker") && !e.target.closest("#emoji-btn")) { $("#emoji-picker").classList.add("hidden"); }
        if (!e.target.closest("#gif-picker") && !e.target.closest("#gif-btn")) { $("#gif-picker").classList.add("hidden"); }
        if (!e.target.closest("#sticker-picker") && !e.target.closest("#sticker-btn")) { $("#sticker-picker").classList.add("hidden"); }
    });

    $("#ctx-reply").addEventListener("click", e => {
        e.stopPropagation();
        if (!contextMsgId) return;
        const row = document.querySelector(`[data-msg-id="${contextMsgId}"]`);
        const sender = row?.querySelector(".message-sender")?.textContent || "";
        let text = row?.querySelector(".message-text")?.textContent || "";
        if (decryptedMsgCache[contextMsgId]) text = decryptedMsgCache[contextMsgId];
        else if (text.includes(":") && text.length > 30 && !text.includes(" ")) text = "[зашифровано]";
        replyToId = contextMsgId;
        $("#reply-preview-author").textContent = sender;
        $("#reply-preview-text").textContent = text.slice(0, 80);
        $("#reply-preview").classList.remove("hidden");
        $("#message-input").focus();
    });
    $("#reply-cancel-btn").addEventListener("click", () => { replyToId = null; $("#reply-preview").classList.add("hidden"); });

    $("#ctx-edit").addEventListener("click", e => {
        e.stopPropagation();
        if (!contextMsgId) return;
        const row = document.querySelector(`[data-msg-id="${contextMsgId}"]`);
        const text = row?.querySelector(".message-text")?.textContent || "";
        editingMsgId = contextMsgId;
        $("#edit-preview-text").textContent = text.slice(0, 80);
        $("#edit-preview").classList.remove("hidden");
        $("#message-input").value = text;
        $("#message-input").focus();
    });
    $("#edit-cancel-btn").addEventListener("click", () => { editingMsgId = null; $("#edit-preview").classList.add("hidden"); $("#message-input").value = ""; });

    $("#ctx-delete").addEventListener("click", async e => {
        e.stopPropagation();
        if (!contextMsgId || !confirm("Удалить сообщение?")) return;
        try { await api(`/api/messages/${contextMsgId}`, { method: "DELETE" }); toast("Удалено", "info"); } catch (err) { toast(err.message || "Ошибка", "error"); }
    });

    $("#ctx-react").addEventListener("click", e => {
        e.stopPropagation();
        const picker = $("#reaction-picker");
        picker.classList.remove("hidden");
        const rect = e.target.getBoundingClientRect();
        picker.style.left = Math.max(0, Math.min(rect.left, window.innerWidth - 300)) + "px";
        picker.style.top = Math.max(0, rect.top - 50) + "px";
    });
    $$(".rp-item").forEach(btn => btn.addEventListener("click", async e => {
        e.stopPropagation();
        const emoji = btn.dataset.emoji;
        if (!contextMsgId || !emoji) return;
        try { await api(`/api/messages/${contextMsgId}/reactions`, { method: "POST", body: { emoji } }); } catch {}
        $("#reaction-picker").classList.add("hidden");
        $("#context-menu").classList.add("hidden");
    }));

    // Copy message
    $("#ctx-copy").addEventListener("click", e => {
        e.stopPropagation();
        if (!contextMsgId) return;
        let text = "";
        const row = document.querySelector(`[data-msg-id="${contextMsgId}"]`);
        if (row) text = row.querySelector(".message-text")?.textContent || "";
        if (decryptedMsgCache[contextMsgId]) text = decryptedMsgCache[contextMsgId];
        navigator.clipboard.writeText(text).then(() => toast("Скопировано", "success")).catch(() => toast("Ошибка копирования", "error"));
        $("#context-menu").classList.add("hidden");
    });

    // Forward
    $("#ctx-forward").addEventListener("click", e => {
        e.stopPropagation();
        if (!contextMsgId) return;
        forwardMsgId = contextMsgId;
        $("#context-menu").classList.add("hidden");
        renderForwardChatList();
        openModal("#modal-forward");
    });

    async function renderForwardChatList() {
        const list = $("#forward-chat-list"); list.innerHTML = '<div class="spinner" style="margin:auto"></div>';
        try {
            const allChats = chats.filter(c => String(c.id) !== String(currentChatId));
            list.innerHTML = "";
            if (!allChats.length) { list.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:20px">Нет других чатов</p>'; return; }
            for (const chat of allChats) {
                const item = document.createElement("div");
                item.className = "forward-chat-item";
                const col = randomColor(chat.name);
                const avUser = chat.other_user || { display_name: chat.name, avatar_url: chat.avatar_url };
                item.innerHTML = `<div class="chat-avatar" style="width:40px;height:40px;font-size:14px;${avUser?.avatar_url ? "" : "background:" + col}">${avatarHtml(avUser)}</div><div><div style="font-weight:600;font-size:14px">${esc(chat.name)}</div></div>`;
                item.addEventListener("click", async () => {
                    if (!confirm(`Переслать в "${chat.name}"?`)) return;
                    try { await api(`/api/messages/${forwardMsgId}/forward`, { method: "POST", body: { chat_id: chat.id } }); closeModal(); toast("Переслано", "success"); } catch { toast("Ошибка", "error"); }
                });
                list.appendChild(item);
            }
        } catch { list.innerHTML = '<p style="text-align:center;color:var(--danger);padding:20px">Ошибка</p>'; }
    }

    // Emoji Picker
    let emojiBuilt = false;
    function buildEmojiPicker() {
        if (emojiBuilt) return; emojiBuilt = true;
        const body = $("#emoji-picker-body"); body.innerHTML = "";
        for (const [cat, emojis] of Object.entries(EMOJI_DATA)) {
            const catEl = document.createElement("div");
            catEl.className = "emoji-category"; catEl.textContent = cat;
            body.appendChild(catEl);
            for (const e of emojis) {
                const btn = document.createElement("button");
                btn.className = "emoji-item"; btn.textContent = e;
                btn.addEventListener("click", ev => { ev.stopPropagation(); insertEmoji(e); });
                body.appendChild(btn);
            }
        }
    }
    function insertEmoji(e) {
        const input = $("#message-input");
        const start = input.selectionStart, end = input.selectionEnd;
        input.value = input.value.slice(0, start) + e + input.value.slice(end);
        input.selectionStart = input.selectionEnd = start + e.length;
        input.focus();
        input.dispatchEvent(new Event("input"));
    }
    $("#emoji-btn").addEventListener("click", e => {
        e.stopPropagation();
        buildEmojiPicker();
        const picker = $("#emoji-picker");
        picker.classList.toggle("hidden");
    });
    $("#emoji-search")?.addEventListener("input", () => {
        const q = $("#emoji-search").value.toLowerCase();
        $$("#emoji-picker-body .emoji-item").forEach(btn => {
            const match = btn.textContent.includes(q) || btn.closest(".emoji-category")?.textContent.includes(q);
            btn.style.display = (!q || match) ? "" : "none";
        });
        $$("#emoji-picker-body .emoji-category").forEach(cat => {
            let next = cat.nextElementSibling;
            let hasVisible = false;
            while (next && !next.classList.contains("emoji-category")) {
                if (next.style.display !== "none") hasVisible = true;
                next = next.nextElementSibling;
            }
            cat.style.display = (!q || hasVisible) ? "" : "none";
        });
    });

    // Send
    async function sendMessage() {
        const text = $("#message-input").value.trim();
        if (!text || !currentChatId) return;

        if (editingMsgId) {
            try {
                const body = { content: text };
                const chat = chats.find(c => String(c.id) === String(currentChatId));
                if (chat?.other_user?.id) {
                    const [rD, sD] = await Promise.all([api(`/api/users/${chat.other_user.id}/public-key`), api(`/api/users/${currentUser.id}/public-key`)]);
                    if (rD.public_key && sD.public_key) {
                        const enc = await CryptoManager.encryptMessageMulti(text, { encrypted_key: rD.public_key, sender_encrypted_key: sD.public_key });
                        body.content = `${enc.iv}:${enc.content}`;
                        body.encrypted_key = enc.encrypted_key;
                        body.sender_encrypted_key = enc.sender_encrypted_key;
                    }
                }
                await api(`/api/messages/${editingMsgId}`, { method: "PUT", body });
                editingMsgId = null; $("#edit-preview").classList.add("hidden");
            } catch { toast("Ошибка редактирования", "error"); }
            $("#message-input").value = ""; $("#message-input").style.height = "auto";
            return;
        }

        $("#message-input").value = ""; $("#message-input").style.height = "auto";
        const payload = { chat_id: currentChatId, content: text, message_type: "text" };
        if (replyToId) { payload.reply_to_id = replyToId; replyToId = null; $("#reply-preview").classList.add("hidden"); }

        const chat = chats.find(c => String(c.id) === String(currentChatId));
        if (chat?.other_user?.id) {
            try {
                const [rD, sD] = await Promise.all([api(`/api/users/${chat.other_user.id}/public-key`), api(`/api/users/${currentUser.id}/public-key`)]);
                if (rD.public_key && sD.public_key) {
                    const enc = await CryptoManager.encryptMessageMulti(text, { encrypted_key: rD.public_key, sender_encrypted_key: sD.public_key });
                    payload.content = `${enc.iv}:${enc.content}`;
                    payload.encrypted_key = enc.encrypted_key;
                    payload.sender_encrypted_key = enc.sender_encrypted_key;
                } else { toast("Нет публичного ключа получателя", "error"); return; }
            } catch { toast("Ошибка шифрования", "error"); return; }
        }
        try { ws.send(JSON.stringify({ type: "message", ...payload })); } catch { toast("Ошибка отправки", "error"); }
    }
    $("#send-btn").addEventListener("click", sendMessage);
    $("#message-input").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
    $("#message-input").addEventListener("input", () => {
        $("#message-input").style.height = "auto";
        $("#message-input").style.height = Math.min($("#message-input").scrollHeight, 120) + "px";
        if (currentChatId && ws?.readyState === WebSocket.OPEN && !typingSendTimeout) {
            ws.send(JSON.stringify({ type: "typing", chat_id: currentChatId }));
            typingSendTimeout = setTimeout(() => { typingSendTimeout = null; }, 2000);
        }
    });

    // File Upload
    $("#attach-btn").addEventListener("click", () => $("#file-input").click());
    $("#file-input").addEventListener("change", async () => {
        const f = $("#file-input").files[0]; if (!f || !currentChatId) return; $("#file-input").value = "";
        if (f.size > 100*1024*1024) { toast("Макс 100 МБ", "error"); return; }
        toast("Загрузка...", "info");
        const fd = new FormData(); fd.append("file", f);
        try {
            const r = await fetch(`${API}/api/upload`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd });
            const d = await r.json();
            ws.send(JSON.stringify({ type: "message", chat_id: currentChatId, content: f.name, message_type: "file", file_url: d.url, file_name: f.name }));
        } catch { toast("Ошибка загрузки", "error"); }
    });

    // Lightbox
    function showLightbox(src) { $("#lightbox-img").src = src; $("#modal-lightbox").classList.remove("hidden"); $("#modal-overlay").classList.remove("hidden"); }
    $("#lightbox-close").addEventListener("click", () => { $("#modal-lightbox").classList.add("hidden"); $("#modal-overlay").classList.add("hidden"); });

    // New Chat
    let isGroupMode = false;
    async function renderUserList() {
        try {
            const users = await api("/api/users");
            const list = $("#user-list"); list.innerHTML = "";
            if (!users.length) { list.innerHTML = '<p style="text-align:center;color:var(--text-muted);padding:20px">Нет пользователей</p>'; return; }
            for (const u of users) {
                const item = document.createElement("div"); item.className = "user-list-item";
                const hasAvatar = !!u.avatar_url;
                item.innerHTML = `<div class="chat-avatar" style="${hasAvatar ? "" : "background:" + randomColor(u.display_name) + ";width:40px;height:40px;font-size:14px"}">${avatarHtml(u)}</div><div><div style="font-weight:600;font-size:14px">${esc(u.display_name)}</div><div style="font-size:12px;color:${u.online ? "var(--success)" : "var(--text-muted)"}">${u.online ? "в сети" : "оффлайн"}</div></div>`;
                item.addEventListener("click", () => {
                    if (!isGroupMode) { list.querySelectorAll(".user-list-item").forEach(el => el.classList.remove("selected")); selectedUsers.clear(); }
                    if (item.classList.contains("selected")) { item.classList.remove("selected"); selectedUsers.delete(u.id); }
                    else { item.classList.add("selected"); selectedUsers.add(u.id); }
                });
                list.appendChild(item);
            }
        } catch {}
    }
    $("#new-chat-btn").addEventListener("click", () => {
        selectedUsers.clear(); isGroupMode = false;
        $("#group-toggle-cb").checked = false;
        $("#group-name-group").classList.add("hidden");
        $("#group-name-input").value = "";
        openModal("#modal-new-chat"); renderUserList();
    });
    $("#group-toggle-cb").addEventListener("change", e => {
        isGroupMode = e.target.checked;
        $("#group-name-group").classList.toggle("hidden", !isGroupMode);
    });

    let creating = false;
    $("#modal-create").addEventListener("click", async () => {
        if (!selectedUsers.size || creating) return; creating = true;
        try {
            const body = { member_ids: Array.from(selectedUsers) };
            if (isGroupMode) body.name = $("#group-name-input").value.trim() || "Группа";
            const chat = await api("/api/chats", { method: "POST", body });
            closeModal(); await loadChats();
            const nc = chats.find(c => String(c.id) === String(chat.id)); if (nc) openChat(nc);
        } catch { toast("Ошибка", "error"); } finally { creating = false; }
    });

    // Header click -> user profile
    $("#chat-header-clickable")?.addEventListener("click", () => {
        if (!currentChatId) return;
        const chat = chats.find(c => String(c.id) === String(currentChatId));
        if (chat?.other_user?.id) showUserProfile(chat.other_user.id);
    });

    // Init
    if (token) startApp();
})();
