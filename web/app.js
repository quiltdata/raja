// RAJA Web Interface - Application Logic

// Global state
let issuedToken = null;
let issuedScopes = [];

// Get API base URL from config (injected by CDK deployment)
const API_BASE_URL = window.RAJA_CONFIG ? window.RAJA_CONFIG.apiUrl : 'http://localhost:3000';

// Utility Functions
function showElement(id) {
    document.getElementById(id).classList.remove('hidden');
}

function hideElement(id) {
    document.getElementById(id).classList.add('hidden');
}

function showLoading(buttonElement) {
    buttonElement.disabled = true;
    buttonElement.dataset.originalText = buttonElement.textContent;
    buttonElement.textContent = 'Loading...';
}

function hideLoading(buttonElement) {
    buttonElement.disabled = false;
    buttonElement.textContent = buttonElement.dataset.originalText || buttonElement.textContent;
}

// Token Issuance Functions
function setPrincipal(principal) {
    document.getElementById('principal').value = principal;
}

async function requestToken() {
    const principal = document.getElementById('principal').value.trim();
    const button = event.target;

    if (!principal) {
        showError('token-error', 'Please enter a principal name');
        return;
    }

    hideElement('token-result');
    hideElement('token-error');
    showLoading(button);

    try {
        const response = await fetch(`${API_BASE_URL}/token`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ principal })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || data.message || 'Failed to request token');
        }

        // Store token globally
        issuedToken = data.token;
        issuedScopes = data.scopes || [];

        // Display token (truncated for readability)
        const tokenDisplay = document.getElementById('token-display');
        tokenDisplay.textContent = issuedToken;

        // Display scopes
        const scopesDisplay = document.getElementById('scopes-display');
        if (issuedScopes.length > 0) {
            scopesDisplay.innerHTML = issuedScopes.map(scope =>
                `<div class="scope-item">${escapeHtml(scope)}</div>`
            ).join('');
        } else {
            scopesDisplay.innerHTML = '<div class="scope-item">No scopes granted</div>';
        }

        showElement('token-result');
    } catch (error) {
        showError('token-error', `Error: ${error.message}`);
    } finally {
        hideLoading(button);
    }
}

function copyToken() {
    if (!issuedToken) return;

    navigator.clipboard.writeText(issuedToken).then(() => {
        const button = event.target;
        const originalText = button.textContent;
        button.textContent = 'Copied!';
        setTimeout(() => {
            button.textContent = originalText;
        }, 2000);
    }).catch(err => {
        alert('Failed to copy token: ' + err);
    });
}

// Authorization Testing Functions
function useIssuedToken() {
    if (!issuedToken) {
        alert('Please request a token in Step 1 first');
        return;
    }
    document.getElementById('auth-token').value = issuedToken;
}

function setAuthRequest(resourceType, resourceId, action) {
    document.getElementById('resource-type').value = resourceType;
    document.getElementById('resource-id').value = resourceId;
    document.getElementById('action').value = action;
}

async function checkAuthorization() {
    const token = document.getElementById('auth-token').value.trim();
    const resourceType = document.getElementById('resource-type').value.trim();
    const resourceId = document.getElementById('resource-id').value.trim();
    const action = document.getElementById('action').value.trim();
    const button = event.target;

    if (!token) {
        showError('auth-error', 'Please enter a token');
        return;
    }

    if (!resourceType || !resourceId || !action) {
        showError('auth-error', 'Please fill in all request fields');
        return;
    }

    hideElement('auth-result');
    hideElement('auth-error');
    showLoading(button);

    try {
        const response = await fetch(`${API_BASE_URL}/authorize`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                token: token,
                request: {
                    resource_type: resourceType,
                    resource_id: resourceId,
                    action: action
                }
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || data.message || 'Authorization check failed');
        }

        // Display decision
        const decisionElement = document.getElementById('auth-decision');
        const allowed = data.allowed;

        decisionElement.className = 'decision ' + (allowed ? 'decision-allow' : 'decision-deny');
        decisionElement.textContent = allowed ? '✓ ALLOWED' : '✗ DENIED';

        // Display reason
        document.getElementById('auth-reason').textContent = data.reason || 'No reason provided';

        // Display matched scope if present
        const matchedScopeContainer = document.getElementById('auth-matched-scope-container');
        if (data.matched_scope) {
            document.getElementById('auth-matched-scope').textContent = data.matched_scope;
            showElement('auth-matched-scope-container');
        } else {
            hideElement('auth-matched-scope-container');
        }

        showElement('auth-result');
    } catch (error) {
        showError('auth-error', `Error: ${error.message}`);
    } finally {
        hideLoading(button);
    }
}

// Token Introspection Functions
function useIssuedTokenForIntrospect() {
    if (!issuedToken) {
        alert('Please request a token in Step 1 first');
        return;
    }
    document.getElementById('introspect-token').value = issuedToken;
}

async function introspectToken() {
    const token = document.getElementById('introspect-token').value.trim();
    const button = event.target;

    if (!token) {
        showError('introspect-error', 'Please enter a token to inspect');
        return;
    }

    hideElement('introspect-result');
    hideElement('introspect-error');
    showLoading(button);

    try {
        const response = await fetch(`${API_BASE_URL}/introspect?token=${encodeURIComponent(token)}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || data.message || 'Failed to introspect token');
        }

        // Display claims as pretty-printed JSON
        const claimsElement = document.getElementById('introspect-claims');
        claimsElement.textContent = JSON.stringify(data.claims || data, null, 2);

        showElement('introspect-result');
    } catch (error) {
        showError('introspect-error', `Error: ${error.message}`);
    } finally {
        hideLoading(button);
    }
}

// Error Handling
function showError(elementId, message) {
    const errorElement = document.getElementById(elementId);
    errorElement.textContent = message;
    showElement(elementId);
}

// Utility: Escape HTML to prevent XSS
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    console.log('RAJA Demo Interface Loaded');
    console.log('API Base URL:', API_BASE_URL);

    // Check if API is configured
    if (!window.RAJA_CONFIG || !window.RAJA_CONFIG.apiUrl) {
        console.warn('API URL not configured. Using default:', API_BASE_URL);
    }
});
