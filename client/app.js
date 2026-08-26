(() => {
    const API = "";
    let token = localStorage.getItem("token");
    let currentUser = null;
    let chats = [];
    let currentChatId = null;
    let ws = null;
    let selectedUsers = new Set();
    let typingTimeout = null;
    let typingSendTimeout = null;
    let decryptedCache = {};
    let isMobile = window.innerWidth <= 768;
    let chatInterval = null;
    let loadChatAbort = null;

    const $ = (s) => document.querySelector(s);
    const $$ = (s) => document.querySelectorAll(s);

    const authScreen = $("#auth-screen");
    const appScreen = $("#app-screen");
    const loginForm = $("#login-form");
    const registerForm = $("#register-form");
    const chatListEl = $("#chat-list");
    const chatArea = $("#chat-area");
    const emptyState = $("#empty-state");
    const chatView = $("#chat-view");
    const messagesContainer = $("#messages-container");
    const messageInput = $("#message-input");
    const sendBtn = $("#send-btn");
    const typingIndicator = $("#typing-indicator");
    const newChatModal = $("#new-chat-modal");
    const userListEl = $("#user-list");
    const fileInput = $("#file-input");
    const searchInput = $("#search-input");
    const sidebar = $("#sidebar");
    const backBtn = $("#back-btn");
    const connectionBanner = $("#connection-banner");
    const scrollBottomBtn = $("#scroll-bottom-btn");

    window.addEventListener("resize", () => {
        isMobile = window.innerWidth <= 768;
    });

    function showChatView() {
        if (isMobile) {
            sidebar.classList.add("hidden");
            chatArea.classList.remove("hidden");
        }
    }

    function showSidebar() {
        if (isMobile) {
            chatArea.classList.add("hidden");
            sidebar.classList.remove("hidden");
        }
    }

    // --- Toast ---

    function toast(message, type = "info") {
        const el = document.createElement("div");
        el.className = `toast ${type}`;
        el.textContent = message;
        $("#toast-container").appendChild(el);
        setTimeout(() => el.remove(), 3500);
    }

    // --- Helpers ---

    function getInitials(name) {
        if (!name) return "?";
        return name.split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2);
    }

    function randomColor(str) {
        const colors = [
            "#6c5ce7", "#00b894", "#e17055", "#0984e3",
            "#d63031", "#e84393", "#00cec9", "#fdcb6e",
            "#a29bfe", "#55a3e8", "#ff7675", "#fab1a0",
        ];
        let hash = 0;
        for (let i = 0; i < (str || "").length; i++) hash = str.charCodeAt(i) + ((hash << 5) - hash);
        return colors[Math.abs(hash) % colors.length];
    }

    function avatarHtml(user, size = 48) {
        if (user?.avatar_url) {
            const src = escapeAttr(user.avatar_url);
            return `<img src="${src}" alt="" width="${size}" height="${size}">`;
        }
        const name = user?.display_name || user?.name || "?";
        return getInitials(name);
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    function escapeAttr(text) {
        return (text || "").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function formatTime(iso) {
        if (!iso) return "";
        const d = new Date(iso);
        const now = new Date();
        const hours = d.getHours().toString().padStart(2, "0");
        const mins = d.getMinutes().toString().padStart(2, "0");
        if (d.toDateString() === now.toDateString()) return `${hours}:${mins}`;
        const day = d.getDate().toString().padStart(2, "0");
        const month = (d.getMonth() + 1).toString().padStart(2, "0");
        return `${day}.${month} ${hours}:${mins}`;
    }

    function formatUptime(seconds) {
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        if (h > 0) return `${h}ч ${m}м`;
        return `${m}м`;
    }

    async function api(path, opts = {}) {
        const headers = { ...opts.headers };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        if (opts.body && !(opts.body instanceof FormData)) {
            headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(opts.body);
        }
        const res = await fetch(`${API}${path}`, { ...opts, headers });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: "Error" }));
            throw new Error(err.detail || "Request failed");
        }
        return res.json();
    }

    // --- Decryption ---

    async function decryptMsg(msg) {
        if (decryptedCache[msg.id]) return decryptedCache[msg.id];
        if (!msg.encrypted_key && !msg.sender_encrypted_key) return msg;
        if (msg.message_type !== "text") return msg;

        const parts = (msg.content || "").split(":");
        if (parts.length < 2) return msg;
        const iv = parts[0];
        const encContent = parts.slice(1).join(":");
        if (!iv || !encContent) return msg;

        const isOwn = String(msg.sender_id) === String(currentUser.id);
        const keysToTry = isOwn
            ? [msg.sender_encrypted_key, msg.encrypted_key].filter(Boolean)
            : [msg.encrypted_key, msg.sender_encrypted_key].filter(Boolean);

        if (keysToTry.length === 0) return msg;

        for (const key of keysToTry) {
            try {
                const decrypted = await CryptoManager.decryptMessage(encContent, key, iv);
                const result = { ...msg, content: decrypted };
                decryptedCache[msg.id] = result;
                return result;
            } catch (e) {
                // Try next key
            }
        }
        return { ...msg, content: "\u{1F512} [Не удалось расшифровать]" };
    }

    // --- Auth ---

    $$(".auth-tab").forEach(tab => {
        tab.addEventListener("click", () => {
            $$(".auth-tab").forEach(t => t.classList.remove("active"));
            tab.classList.add("active");
            const target = tab.dataset.tab;
            $$(".auth-form").forEach(f => f.classList.remove("active"));
            $(`#${target}-form`).classList.add("active");
        });
    });

    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errEl = $("#login-error");
        errEl.textContent = "";
        try {
            const data = await api("/api/login", {
                method: "POST",
                body: {
                    username: $("#login-username").value.trim(),
                    password: $("#login-password").value,
                },
            });
            onAuth(data);
        } catch (err) {
            errEl.textContent = err.message;
        }
    });

    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const errEl = $("#reg-error");
        errEl.textContent = "";
        const username = $("#reg-username").value.trim();
        const displayName = $("#reg-displayname").value.trim();
        const password = $("#reg-password").value;

        if (!username || !displayName || !password) {
            errEl.textContent = "Заполните все поля";
            return;
        }

        try {
            const keyPair = await CryptoManager.generateKeyPair();
            localStorage.setItem("private_key", keyPair.privateKey);

            const data = await api("/api/register", {
                method: "POST",
                body: {
                    username,
                    display_name: displayName,
                    password,
                    public_key: keyPair.publicKey,
                },
            });
            onAuth(data);
        } catch (err) {
            errEl.textContent = err.message;
        }
    });

    function onAuth(data) {
        token = data.token;
        currentUser = data.user;
        localStorage.setItem("token", token);
        localStorage.setItem("user", JSON.stringify(currentUser));
        localStorage.setItem("user_id", String(currentUser.id));

        if (!localStorage.getItem("private_key")) {
            CryptoManager.generateKeyPair().then(kp => {
                localStorage.setItem("private_key", kp.privateKey);
            });
        }

        startApp();
    }

    // --- App Init ---

    async function startApp() {
        if (!currentUser) {
            try {
                currentUser = await api("/api/me");
            } catch {
                logout();
                return;
            }
        }

        authScreen.style.display = "none";
        appScreen.classList.add("active");

        $("#my-name").textContent = currentUser.display_name;
        updateMyAvatar();

        if (!isMobile) {
            sidebar.classList.remove("hidden");
            chatArea.classList.remove("hidden");
        } else {
            sidebar.classList.remove("hidden");
            chatArea.classList.add("hidden");
        }

        connectWebSocket();
        await loadChats();
        renderUserList();

        if (chatInterval) clearInterval(chatInterval);
        chatInterval = setInterval(loadChats, 10000);
    }

    function updateMyAvatar() {
        const av = $("#my-avatar");
        if (currentUser.avatar_url) {
            av.innerHTML = avatarHtml(currentUser, 40);
            av.style.background = "transparent";
        } else {
            av.textContent = getInitials(currentUser.display_name);
            av.style.background = randomColor(currentUser.display_name);
        }
    }

    function logout() {
        token = null;
        currentUser = null;
        currentChatId = null;
        localStorage.removeItem("token");
        localStorage.removeItem("user");
        localStorage.removeItem("user_id");
        localStorage.removeItem("private_key");
        decryptedCache = {};
        if (ws) ws.close();
        if (chatInterval) clearInterval(chatInterval);
        appScreen.classList.remove("active");
        authScreen.style.display = "flex";
        $$(".auth-form").forEach(f => f.classList.remove("active"));
        $("#login-form").classList.add("active");
        $$(".auth-tab").forEach(t => t.classList.remove("active"));
        $$(".auth-tab")[0].classList.add("active");
    }

    $("#logout-btn").addEventListener("click", logout);

    // --- Avatar upload ---

    $("#avatar-edit-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        $("#avatar-input").click();
    });

    $("#avatar-input").addEventListener("change", async () => {
        const file = $("#avatar-input").files[0];
        if (!file) return;
        $("#avatar-input").value = "";
        if (file.size > 5 * 1024 * 1024) {
            toast("Максимум 5 МБ", "error");
            return;
        }

        const formData = new FormData();
        formData.append("file", file);

        try {
            const data = await api("/api/me/avatar", { method: "POST", body: formData });
            currentUser = data;
            localStorage.setItem("user", JSON.stringify(currentUser));
            updateMyAvatar();
            toast("Аватар обновлён", "success");
        } catch (err) {
            toast("Ошибка загрузки аватара", "error");
        }
    });

    // --- Profile ---

    const profileModal = $("#profile-modal");

    $("#profile-btn").addEventListener("click", () => {
        $("#profile-displayname").value = currentUser.display_name;
        $("#profile-password").value = "";
        $("#profile-error").textContent = "";
        profileModal.classList.add("active");
    });

    $("#profile-cancel").addEventListener("click", () => profileModal.classList.remove("active"));
    profileModal.addEventListener("click", (e) => { if (e.target === profileModal) profileModal.classList.remove("active"); });

    $("#profile-save").addEventListener("click", async () => {
        const displayName = $("#profile-displayname").value.trim();
        const password = $("#profile-password").value;
        const errEl = $("#profile-error");
        errEl.textContent = "";

        if (!displayName) { errEl.textContent = "Имя не может быть пустым"; return; }

        try {
            const body = { display_name: displayName };
            if (password.length > 0) {
                if (password.length < 6) { errEl.textContent = "Пароль минимум 6 символов"; return; }
                body.password = password;
            }
            const data = await api("/api/me", { method: "PUT", body });
            currentUser = data;
            localStorage.setItem("user", JSON.stringify(currentUser));
            $("#my-name").textContent = currentUser.display_name;
            updateMyAvatar();
            profileModal.classList.remove("active");
            toast("Профиль обновлён", "success");
        } catch (err) {
            errEl.textContent = err.message;
        }
    });

    // --- Host Info ---

    const hostInfoModal = $("#host-info-modal");

    $("#host-info-btn").addEventListener("click", async () => {
        hostInfoModal.classList.add("active");
        const content = $("#host-info-content");
        content.innerHTML = '<div class="spinner"></div>';

        try {
            const info = await api("/api/host-info");
            const tunnelUrl = info.tunnel_url || "Не настроен";
            const isPermanent = info.permanent;

            content.innerHTML = `
                <div class="host-info-field">
                    <div class="host-info-label">Ссылка на сервер</div>
                    <div class="host-info-value url" id="host-url-value">${escapeHtml(tunnelUrl)}</div>
                    <button class="host-info-copy" id="host-copy-btn">&#128203; Копировать ссылку</button>
                </div>
                <div class="host-info-field">
                    <div class="host-info-label">Тип туннеля</div>
                    <div class="host-info-value">${isPermanent ? "Постоянный" : "Временный (меняется при перезапуске)"}</div>
                </div>
                <div class="host-info-field">
                    <div class="host-info-label">Время работы</div>
                    <div class="host-info-value">${formatUptime(info.uptime_seconds)}</div>
                </div>
                <div class="host-info-field">
                    <div class="host-info-label">Онлайн пользователей</div>
                    <div class="host-info-value">${info.online_users}</div>
                </div>
                <div class="host-info-field">
                    <div class="host-info-label">Поддержать проект</div>
                    <div class="host-info-value" style="font-size:13px; color: var(--text-secondary);">
                        Запустите свой сервер: скопируйте проект и запустите run.bat
                    </div>
                </div>
            `;

            $("#host-copy-btn").addEventListener("click", async () => {
                try {
                    await navigator.clipboard.writeText(tunnelUrl);
                    toast("Ссылка скопирована", "success");
                } catch {
                    toast("Не удалось скопировать", "error");
                }
            });

            $("#host-url-value").addEventListener("click", () => {
                if (tunnelUrl && tunnelUrl !== "Не настроен") {
                    window.open(tunnelUrl, "_blank");
                }
            });
        } catch (err) {
            content.innerHTML = `<div class="host-info-loading">Ошибка загрузки</div>`;
        }
    });

    $("#host-info-close").addEventListener("click", () => hostInfoModal.classList.remove("active"));
    hostInfoModal.addEventListener("click", (e) => { if (e.target === hostInfoModal) hostInfoModal.classList.remove("active"); });

    // --- Chat menu ---

    const chatMenuModal = $("#chat-menu-modal");

    $("#chat-menu-btn").addEventListener("click", () => {
        if (!currentChatId) return;
        chatMenuModal.classList.add("active");
    });

    $("#chat-menu-cancel").addEventListener("click", () => chatMenuModal.classList.remove("active"));
    chatMenuModal.addEventListener("click", (e) => { if (e.target === chatMenuModal) chatMenuModal.classList.remove("active"); });

    $("#chat-menu-delete").addEventListener("click", async () => {
        if (!currentChatId) return;
        chatMenuModal.classList.remove("active");

        const chatIdToDelete = currentChatId;
        currentChatId = null;
        chatView.style.display = "none";
        emptyState.style.display = "flex";
        showSidebar();

        if (ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "chat_deleted", chat_id: chatIdToDelete }));
        }

        try {
            await api(`/api/chats/${chatIdToDelete}`, { method: "DELETE" });
            await loadChats();
            toast("Чат удалён", "info");
        } catch (err) {
            await loadChats();
        }
    });

    // --- Back button ---

    backBtn.addEventListener("click", () => {
        currentChatId = null;
        chatView.style.display = "none";
        emptyState.style.display = "flex";
        showSidebar();
        renderChatList(searchInput.value);
    });

    // --- Chat search ---

    const chatSearchBar = $("#chat-search-bar");
    const chatSearchInput = $("#chat-search-input");

    $("#search-chat-btn").addEventListener("click", () => {
        chatSearchBar.classList.toggle("hidden");
        if (!chatSearchBar.classList.contains("hidden")) {
            chatSearchInput.focus();
        }
    });

    $("#chat-search-close").addEventListener("click", () => {
        chatSearchBar.classList.add("hidden");
        chatSearchInput.value = "";
    });

    let chatSearchTimeout = null;
    chatSearchInput.addEventListener("input", () => {
        clearTimeout(chatSearchTimeout);
        chatSearchTimeout = setTimeout(async () => {
            const q = chatSearchInput.value.trim();
            if (!q || !currentChatId) return;
            try {
                const results = await api(`/api/chats/${currentChatId}/search?q=${encodeURIComponent(q)}`);
                if (results.length > 0) {
                    toast(`Найдено: ${results.length} сообщений`, "info");
                } else {
                    toast("Ничего не найдено", "info");
                }
            } catch (e) {}
        }, 500);
    });

    // --- Scroll to bottom ---

    messagesContainer.addEventListener("scroll", () => {
        const diff = messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight;
        if (diff > 200) {
            scrollBottomBtn.classList.remove("hidden");
        } else {
            scrollBottomBtn.classList.add("hidden");
        }
    });

    scrollBottomBtn.addEventListener("click", () => {
        scrollToBottom();
        scrollBottomBtn.classList.add("hidden");
    });

    // --- WebSocket ---

    function connectWebSocket() {
        if (ws) ws.close();
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        ws = new WebSocket(`${protocol}//${location.host}/ws?token=${token}`);

        ws.onopen = () => {
            connectionBanner.classList.add("hidden");
        };

        ws.onmessage = (e) => {
            try {
                handleWsMessage(JSON.parse(e.data));
            } catch (err) {}
        };

        ws.onclose = () => {
            connectionBanner.classList.remove("hidden");
            setTimeout(connectWebSocket, 3000);
        };

        ws.onerror = () => {};
    }

    function handleWsMessage(data) {
        switch (data.type) {
            case "message":
                onNewMessage(data.message);
                break;
            case "typing":
                onTyping(data);
                break;
            case "read":
                break;
            case "chat_deleted":
                onChatDeleted(data.chat_id);
                break;
            case "profile_update":
                onProfileUpdate(data);
                break;
            case "presence":
                onPresence(data);
                break;
        }
    }

    function onNewMessage(msg) {
        if (String(msg.chat_id) === String(currentChatId)) {
            appendMessage(msg, false);
            scrollToBottom();
            if (String(msg.sender_id) !== String(currentUser.id)) {
                sendReadReceipt();
            }
        }
        loadChats();
    }

    function onTyping(data) {
        if (String(data.chat_id) === String(currentChatId)) {
            typingIndicator.textContent = `${data.user_name} печатает...`;
            clearTimeout(typingTimeout);
            typingTimeout = setTimeout(() => { typingIndicator.textContent = ""; }, 3000);
        }
    }

    function onChatDeleted(chatId) {
        if (String(chatId) === String(currentChatId)) {
            currentChatId = null;
            chatView.style.display = "none";
            emptyState.style.display = "flex";
            showSidebar();
        }
        loadChats();
    }

    function onProfileUpdate(data) {
        const chat = chats.find(c => c.other_user?.id === data.user_id);
        if (chat) {
            chat.other_user.display_name = data.display_name;
            chat.other_user.avatar_url = data.avatar_url;
        }
        if (String(data.user_id) === String(currentUser?.id)) {
            currentUser.display_name = data.display_name;
            currentUser.avatar_url = data.avatar_url;
            localStorage.setItem("user", JSON.stringify(currentUser));
            updateMyAvatar();
        }
        loadChats();
    }

    function onPresence(data) {
        loadChats();
    }

    function sendReadReceipt() {
        if (currentChatId && ws?.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "read", chat_id: currentChatId }));
        }
    }

    // --- Chats ---

    async function loadChats() {
        try {
            chats = await api("/api/chats");
            renderChatList(searchInput.value);
        } catch (err) {}
    }

    async function renderChatList(filter = "") {
        chatListEl.innerHTML = "";
        const filtered = chats.filter(c =>
            c.name.toLowerCase().includes(filter.toLowerCase())
        );

        for (const chat of filtered) {
            const item = document.createElement("div");
            item.className = `chat-item${String(chat.id) === String(currentChatId) ? " active" : ""}`;

            const avatarColor = randomColor(chat.name);
            const isOnline = chat.other_user?.online;
            const lastMsg = chat.last_message;
            const unread = chat.unread || 0;

            let preview = "";
            if (lastMsg) {
                if (lastMsg.message_type === "file") {
                    preview = "\u{1F4CE} " + (lastMsg.file_name || "Файл");
                } else if (lastMsg.encrypted_key || lastMsg.sender_encrypted_key) {
                    const parts = (lastMsg.content || "").split(":");
                    if (parts.length >= 2) {
                        const iv = parts[0];
                        const encContent = parts.slice(1).join(":");
                        const isOwn = String(lastMsg.sender_id) === String(currentUser.id);
                        const keysToTry = isOwn
                            ? [lastMsg.sender_encrypted_key, lastMsg.encrypted_key].filter(Boolean)
                            : [lastMsg.encrypted_key, lastMsg.sender_encrypted_key].filter(Boolean);
                        for (const key of keysToTry) {
                            try {
                                preview = await CryptoManager.decryptMessage(encContent, key, iv);
                                break;
                            } catch (e) {}
                        }
                        if (!preview) preview = "\u{1F512} зашифровано";
                    } else { preview = "\u{1F512} зашифровано"; }
                } else {
                    preview = lastMsg.content || "";
                }
            }

            const avatarUser = chat.other_user || { display_name: chat.name, avatar_url: null };
            const hasAvatar = !!avatarUser.avatar_url;

            item.innerHTML = `
                <div class="chat-avatar" style="${hasAvatar ? "" : "background:" + avatarColor}">
                    ${avatarHtml(avatarUser)}
                    ${isOnline ? '<div class="online-dot"></div>' : ""}
                </div>
                <div class="chat-info">
                    <div class="chat-name">${escapeHtml(chat.name)}</div>
                    <div class="chat-preview">${escapeHtml(preview)}</div>
                </div>
                <div class="chat-meta">
                    <div class="chat-time">${formatTime(lastMsg?.created_at)}</div>
                    ${unread > 0 ? `<div class="unread-badge">${unread > 99 ? "99+" : unread}</div>` : ""}
                </div>
            `;

            item.addEventListener("click", () => openChat(chat));
            chatListEl.appendChild(item);
        }
    }

    searchInput.addEventListener("input", () => renderChatList(searchInput.value));

    // --- Open Chat ---

    async function openChat(chat) {
        if (loadChatAbort) loadChatAbort.abort();
        loadChatAbort = new AbortController();

        currentChatId = chat.id;
        emptyState.style.display = "none";
        chatView.style.display = "flex";
        chatSearchBar.classList.add("hidden");
        chatSearchInput.value = "";

        const av = $("#chat-avatar");
        const avatarUser = chat.other_user || { display_name: chat.name, avatar_url: null };
        if (avatarUser.avatar_url) {
            av.innerHTML = avatarHtml(avatarUser, 40);
            av.style.background = "transparent";
        } else {
            av.textContent = getInitials(chat.name);
            av.style.background = randomColor(chat.name);
        }

        $("#chat-header-name").textContent = chat.name;

        if (chat.other_user) {
            $("#chat-header-status").textContent = chat.other_user.online ? "в сети" : "оффлайн";
            $("#encryption-badge").className = "encryption-badge";
            $("#encryption-badge").innerHTML = "\u{1F512} E2E";
        } else {
            $("#chat-header-status").textContent = `${chat.members?.length || 0} участников`;
            $("#encryption-badge").className = "encryption-badge no-e2e";
            $("#encryption-badge").innerHTML = "\u{26A0} Группа";
        }

        renderChatList(searchInput.value);
        showChatView();

        messagesContainer.innerHTML = '<div class="spinner"></div>';

        try {
            const messages = await api(`/api/chats/${chat.id}/messages`);
            messagesContainer.innerHTML = "";
            for (const msg of messages) {
                await appendMessage(msg, true);
            }
            scrollToBottom();
            sendReadReceipt();
        } catch (err) {
            messagesContainer.innerHTML = '<div class="empty-state"><p>Не удалось загрузить сообщения</p></div>';
        }
    }

    // --- Messages ---

    async function appendMessage(msg, isHistory) {
        msg = await decryptMsg(msg);

        const isOwn = String(msg.sender_id) === String(currentUser.id);
        const row = document.createElement("div");
        row.className = `message-row${isOwn ? " own" : ""}`;
        row.dataset.id = msg.id;

        let contentHtml = "";
        if (msg.message_type === "file") {
            const fileName = msg.file_name || "file";
            const ext = fileName.split(".").pop().toLowerCase();
            const icon = getFileIcon(ext);
            const isImage = ["jpg", "jpeg", "png", "gif", "webp"].includes(ext);
            const fileUrl = msg.file_url || "#";

            if (isImage && !isHistory) {
                contentHtml = `
                    <div class="message-text">
                        ${msg.content !== fileName ? escapeHtml(msg.content) + "<br>" : ""}
                        <a href="${escapeAttr(fileUrl)}" target="_blank">
                            <img class="message-image" src="${escapeAttr(fileUrl)}" alt="${escapeAttr(fileName)}" loading="lazy">
                        </a>
                    </div>
                `;
            } else {
                contentHtml = `
                    <div class="message-text">
                        ${msg.content !== fileName ? escapeHtml(msg.content) + "<br>" : ""}
                        <a class="message-file" href="${escapeAttr(fileUrl)}" target="_blank" download="${escapeAttr(fileName)}">
                            <span class="message-file-icon">${icon}</span>
                            <div class="message-file-info">
                                <div class="message-file-name">${escapeHtml(fileName)}</div>
                            </div>
                        </a>
                    </div>
                `;
            }
        } else {
            contentHtml = `<div class="message-text">${escapeHtml(msg.content)}</div>`;
        }

        row.innerHTML = `
            <div class="message-bubble">
                ${!isOwn ? `<div class="message-sender">${escapeHtml(msg.sender_name)}</div>` : ""}
                ${contentHtml}
                <div class="message-time">${formatTime(msg.created_at)}</div>
            </div>
        `;
        messagesContainer.appendChild(row);
    }

    function getFileIcon(ext) {
        const icons = {
            pdf: "\u{1F4C4}", doc: "\u{1F4DD}", docx: "\u{1F4DD}", txt: "\u{1F4DD}",
            jpg: "\u{1F5BC}", jpeg: "\u{1F5BC}", png: "\u{1F5BC}", gif: "\u{1F5BC}", webp: "\u{1F5BC}", svg: "\u{1F5BC}",
            mp3: "\u{1F3B5}", wav: "\u{1F3B5}", ogg: "\u{1F3B5}", flac: "\u{1F3B5}",
            mp4: "\u{1F3AC}", avi: "\u{1F3AC}", mkv: "\u{1F3AC}", mov: "\u{1F3AC}",
            zip: "\u{1F4E6}", rar: "\u{1F4E6}", "7z": "\u{1F4E6}", tar: "\u{1F4E6}",
            py: "\u{1F40D}", js: "\u{1F4DC}", html: "\u{1F310}", css: "\u{1F3A8}",
            exe: "\u{2699}", msi: "\u{2699}", apk: "\u{1F4F1}",
        };
        return icons[ext] || "\u{1F4C1}";
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        });
    }

    // --- Send ---

    async function sendMessage() {
        const text = messageInput.value.trim();
        if (!text || !currentChatId) return;

        messageInput.value = "";
        messageInput.style.height = "auto";

        const chat = chats.find(c => String(c.id) === String(currentChatId));
        let payload = {
            chat_id: currentChatId,
            content: text,
            message_type: "text",
        };

        if (chat?.other_user?.id) {
            try {
                const [recipientData, senderData] = await Promise.all([
                    api(`/api/users/${chat.other_user.id}/public-key`),
                    api(`/api/users/${currentUser.id}/public-key`),
                ]);

                if (recipientData.public_key && senderData.public_key) {
                    const encrypted = await CryptoManager.encryptMessageMulti(text, {
                        encrypted_key: recipientData.public_key,
                        sender_encrypted_key: senderData.public_key,
                    });
                    payload.content = `${encrypted.iv}:${encrypted.content}`;
                    payload.encrypted_key = encrypted.encrypted_key;
                    payload.sender_encrypted_key = encrypted.sender_encrypted_key;
                } else {
                    return;
                }
            } catch (e) {
                console.error("E2E failed:", e);
                return;
            }
        }

        try {
            ws.send(JSON.stringify({ type: "message", ...payload }));
        } catch (e) {
            toast("Не удалось отправить сообщение", "error");
        }
    }

    sendBtn.addEventListener("click", sendMessage);

    messageInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    messageInput.addEventListener("input", () => {
        messageInput.style.height = "auto";
        messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + "px";

        if (currentChatId && ws?.readyState === WebSocket.OPEN && !typingSendTimeout) {
            ws.send(JSON.stringify({ type: "typing", chat_id: currentChatId }));
            typingSendTimeout = setTimeout(() => { typingSendTimeout = null; }, 2000);
        }
    });

    // --- File upload ---

    $("#attach-btn").addEventListener("click", () => fileInput.click());

    fileInput.addEventListener("change", async () => {
        const file = fileInput.files[0];
        if (!file || !currentChatId) return;
        fileInput.value = "";

        if (file.size > 100 * 1024 * 1024) {
            toast("Максимум 100 МБ", "error");
            return;
        }

        toast("Загрузка файла...", "info");

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch(`${API}/api/upload`, {
                method: "POST",
                headers: { Authorization: `Bearer ${token}` },
                body: formData,
            });
            const data = await res.json();

            ws.send(JSON.stringify({
                type: "message",
                chat_id: currentChatId,
                content: file.name,
                message_type: "file",
                file_url: data.url,
                file_name: file.name,
            }));
        } catch (err) {
            toast("Ошибка загрузки файла", "error");
        }
    });

    // --- New chat modal ---

    let isGroupMode = false;

    async function renderUserList() {
        try {
            const users = await api("/api/users");
            userListEl.innerHTML = "";

            const groupToggle = document.createElement("label");
            groupToggle.className = "group-toggle";
            groupToggle.innerHTML = `<input type="checkbox" id="group-toggle-cb"> Групповой чат`;
            userListEl.appendChild(groupToggle);

            const groupNameGroup = $("#group-name-group");

            $("#group-toggle-cb").addEventListener("change", (e) => {
                isGroupMode = e.target.checked;
                groupNameGroup.style.display = isGroupMode ? "flex" : "none";
                selectedUsers.clear();
                userListEl.querySelectorAll(".user-list-item").forEach(el => el.classList.remove("selected"));
            });

            if (users.length === 0) {
                const empty = document.createElement("div");
                empty.style.cssText = "text-align:center; color:var(--text-muted); padding:20px; font-size:14px;";
                empty.textContent = "Нет других пользователей";
                userListEl.appendChild(empty);
                return;
            }

            for (const u of users) {
                const item = document.createElement("div");
                item.className = "user-list-item";
                const hasAvatar = !!u.avatar_url;
                item.innerHTML = `
                    <div class="chat-avatar" style="${hasAvatar ? "" : "background:" + randomColor(u.display_name) + "; width:40px; height:40px; font-size:14px;"}>
                        ${avatarHtml(u, 40)}
                    </div>
                    <div>
                        <div style="font-weight:600; font-size:14px;">${escapeHtml(u.display_name)}</div>
                        <div style="font-size:12px; color:${u.online ? "var(--success)" : "var(--text-muted)"}">
                            ${u.online ? "в сети" : "оффлайн"}
                        </div>
                    </div>
                `;
                item.addEventListener("click", () => {
                    if (!isGroupMode) {
                        userListEl.querySelectorAll(".user-list-item").forEach(el => el.classList.remove("selected"));
                        selectedUsers.clear();
                        selectedUsers.add(u.id);
                        item.classList.add("selected");
                    } else {
                        if (item.classList.contains("selected")) {
                            item.classList.remove("selected");
                            selectedUsers.delete(u.id);
                        } else {
                            item.classList.add("selected");
                            selectedUsers.add(u.id);
                        }
                    }
                });
                userListEl.appendChild(item);
            }
        } catch (err) {}
    }

    $("#new-chat-btn").addEventListener("click", () => {
        selectedUsers.clear();
        isGroupMode = false;
        $("#group-name-group").style.display = "none";
        $("#group-name-input").value = "";
        newChatModal.classList.add("active");
        renderUserList();
    });

    $("#modal-cancel").addEventListener("click", () => newChatModal.classList.remove("active"));
    newChatModal.addEventListener("click", (e) => { if (e.target === newChatModal) newChatModal.classList.remove("active"); });

    let creatingChat = false;
    $("#modal-create").addEventListener("click", async () => {
        if (selectedUsers.size === 0 || creatingChat) return;
        creatingChat = true;
        const btn = $("#modal-create");
        btn.textContent = "Создание...";
        btn.disabled = true;

        try {
            const body = { member_ids: Array.from(selectedUsers) };
            if (isGroupMode) {
                body.name = $("#group-name-input").value.trim() || "Группа";
            }
            const chat = await api("/api/chats", { method: "POST", body });
            newChatModal.classList.remove("active");
            await loadChats();
            const newChat = chats.find(c => String(c.id) === String(chat.id));
            if (newChat) openChat(newChat);
        } catch (err) {
            toast("Ошибка создания чата", "error");
        } finally {
            creatingChat = false;
            btn.textContent = "Создать";
            btn.disabled = false;
        }
    });

    // --- Init ---

    if (token) {
        startApp();
    }
})();
