const CryptoManager = {
    async generateKeyPair() {
        const keyPair = await window.crypto.subtle.generateKey(
            {
                name: "RSA-OAEP",
                modulusLength: 2048,
                publicExponent: new Uint8Array([1, 0, 1]),
                hash: "SHA-256",
            },
            true,
            ["encrypt", "decrypt"]
        );

        const pubKey = await window.crypto.subtle.exportKey("spki", keyPair.publicKey);
        const privKey = await window.crypto.subtle.exportKey("pkcs8", keyPair.privateKey);

        return {
            publicKey: this.arrayBufferToBase64(pubKey),
            privateKey: this.arrayBufferToBase64(privKey),
        };
    },

    async encryptMessageMulti(plaintext, publicKeys) {
        const aesKey = await window.crypto.subtle.generateKey(
            { name: "AES-GCM", length: 256 },
            true,
            ["encrypt", "decrypt"]
        );

        const iv = window.crypto.getRandomValues(new Uint8Array(12));
        const encoded = new TextEncoder().encode(plaintext);

        const encryptedContent = await window.crypto.subtle.encrypt(
            { name: "AES-GCM", iv },
            aesKey,
            encoded
        );

        const rawAesKey = await window.crypto.subtle.exportKey("raw", aesKey);
        const result = {
            content: this.arrayBufferToBase64(encryptedContent),
            iv: this.arrayBufferToBase64(iv),
        };

        for (const [label, pubKeyBase64] of Object.entries(publicKeys)) {
            if (!pubKeyBase64) continue;
            try {
                const pubKey = await this.importPublicKey(pubKeyBase64);
                const encKey = await window.crypto.subtle.encrypt(
                    { name: "RSA-OAEP" },
                    pubKey,
                    rawAesKey
                );
                result[label] = this.arrayBufferToBase64(encKey);
            } catch (e) {
                console.error(`Failed to encrypt key for ${label}:`, e);
            }
        }

        return result;
    },

    async decryptMessage(encryptedContentBase64, encryptedKeyBase64, ivBase64) {
        const privateKey = await this.getPrivateKey();
        if (!privateKey) throw new Error("No private key");

        const encryptedAesKey = this.base64ToArrayBuffer(encryptedKeyBase64);
        const rawAesKey = await window.crypto.subtle.decrypt(
            { name: "RSA-OAEP" },
            privateKey,
            encryptedAesKey
        );

        const aesKey = await window.crypto.subtle.importKey(
            "raw",
            rawAesKey,
            { name: "AES-GCM", length: 256 },
            false,
            ["decrypt"]
        );

        const iv = this.base64ToArrayBuffer(ivBase64);
        const encryptedContent = this.base64ToArrayBuffer(encryptedContentBase64);

        const decrypted = await window.crypto.subtle.decrypt(
            { name: "AES-GCM", iv },
            aesKey,
            encryptedContent
        );

        return new TextDecoder().decode(decrypted);
    },

    async importPublicKey(base64Key) {
        const keyData = this.base64ToArrayBuffer(base64Key);
        return await window.crypto.subtle.importKey(
            "spki",
            keyData,
            { name: "RSA-OAEP", hash: "SHA-256" },
            false,
            ["encrypt"]
        );
    },

    async getPrivateKey() {
        const privKeyBase64 = localStorage.getItem("private_key");
        if (!privKeyBase64) return null;
        try {
            const keyData = this.base64ToArrayBuffer(privKeyBase64);
            return await window.crypto.subtle.importKey(
                "pkcs8",
                keyData,
                { name: "RSA-OAEP", hash: "SHA-256" },
                false,
                ["decrypt"]
            );
        } catch (e) {
            return null;
        }
    },

    arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = "";
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    },

    base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    },
};
