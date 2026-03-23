// Alfred Settings — Integration Credential Management

const cardsContainer = document.getElementById('integration-cards');

async function loadIntegrations() {
    try {
        const resp = await fetch('/api/integrations');
        const integrations = await resp.json();
        cardsContainer.innerHTML = '';
        integrations.forEach(renderCard);
    } catch (err) {
        cardsContainer.innerHTML = '<p class="settings-error">Failed to load integrations.</p>';
        console.error('Failed to load integrations:', err);
    }
}

function renderCard(integration) {
    const card = document.createElement('div');
    card.className = 'integration-card';
    card.dataset.name = integration.name;

    const fields = integration.schema.fields;
    const fieldNames = Object.keys(fields);

    // Status: check if all required fields are configured
    const allConfigured = fieldNames
        .filter(f => fields[f].required && !fields[f].transient)
        .every(f => integration.configured[f]);

    const statusClass = allConfigured ? 'configured' : 'unconfigured';
    const statusText = allConfigured ? 'Configured' : 'Not configured';

    let fieldsHtml = '';
    for (const [name, field] of Object.entries(fields)) {
        const inputType = field.field_type === 'password' ? 'password' : 'text';
        const required = field.required ? 'required' : '';
        const configured = integration.configured[name];
        const placeholder = configured
            ? (field.field_type === 'password' ? '••••••••' : field.placeholder)
            : field.placeholder;
        const helpHtml = field.help_text
            ? `<span class="field-help">${field.help_text}</span>`
            : '';
        const transientBadge = field.transient
            ? '<span class="field-badge">not stored</span>'
            : '';

        fieldsHtml += `
            <label class="settings-label">
                <span class="label-text">${field.label}${transientBadge}</span>
                <div class="input-wrapper">
                    <input type="${inputType}" name="${name}" placeholder="${placeholder}"
                           ${required} autocomplete="off" data-field="${name}">
                    ${inputType === 'password' ? '<button type="button" class="toggle-vis" title="Toggle visibility">Show</button>' : ''}
                </div>
                ${helpHtml}
            </label>
        `;
    }

    card.innerHTML = `
        <div class="card-header">
            <div>
                <h3>${integration.name.replace(/_/g, ' ')}</h3>
                <span class="card-category">${integration.category}</span>
            </div>
            <span class="card-status ${statusClass}">${statusText}</span>
        </div>
        <div class="card-fields">${fieldsHtml}</div>
        <div class="card-actions">
            <button class="settings-btn primary" data-action="save">Save</button>
            <button class="settings-btn" data-action="test">Test Connection</button>
            <button class="settings-btn danger" data-action="clear">Clear</button>
        </div>
        <div class="card-message" style="display:none;"></div>
    `;

    // Toggle password visibility
    card.querySelectorAll('.toggle-vis').forEach(btn => {
        btn.addEventListener('click', () => {
            const input = btn.parentElement.querySelector('input');
            if (input.type === 'password') {
                input.type = 'text';
                btn.textContent = 'Hide';
            } else {
                input.type = 'password';
                btn.textContent = 'Show';
            }
        });
    });

    // Save
    card.querySelector('[data-action="save"]').addEventListener('click', async () => {
        const body = {};
        card.querySelectorAll('[data-field]').forEach(input => {
            if (input.value) body[input.name] = input.value;
        });
        await apiCall('PUT', `/api/integrations/${integration.name}/credentials`, body, card);
    });

    // Test
    card.querySelector('[data-action="test"]').addEventListener('click', async () => {
        await apiCall('GET', `/api/integrations/${integration.name}/status`, null, card);
    });

    // Clear
    card.querySelector('[data-action="clear"]').addEventListener('click', async () => {
        if (!confirm('Clear all credentials for this integration?')) return;
        await apiCall('DELETE', `/api/integrations/${integration.name}/credentials`, null, card);
    });

    cardsContainer.appendChild(card);
}

async function apiCall(method, url, body, card) {
    const msgEl = card.querySelector('.card-message');
    msgEl.style.display = 'block';
    msgEl.className = 'card-message loading';
    msgEl.textContent = 'Working...';

    try {
        const opts = { method };
        if (body) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
        const resp = await fetch(url, opts);
        const data = await resp.json();

        if (!resp.ok) {
            msgEl.className = 'card-message error';
            msgEl.textContent = data.detail || 'Request failed';
            return;
        }

        if (data.healthy !== undefined) {
            msgEl.className = data.healthy ? 'card-message success' : 'card-message error';
            msgEl.textContent = data.healthy ? 'Connection successful' : 'Connection failed';
        } else {
            msgEl.className = 'card-message success';
            msgEl.textContent = 'Done';
            // Reload to update configured status
            setTimeout(loadIntegrations, 800);
        }
    } catch (err) {
        msgEl.className = 'card-message error';
        msgEl.textContent = 'Network error';
        console.error(err);
    }
}

loadIntegrations();
