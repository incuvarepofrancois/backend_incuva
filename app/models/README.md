# Modèles de données

Ce document décrit le schéma déclaré par les modèles SQLAlchemy de `backend/app/models`. Les types sont les types SQLAlchemy du code ; leur représentation physique peut varier selon le moteur de base de données. `NULL` indique une colonne nullable. Les valeurs par défaut indiquées sont des valeurs définies côté application par SQLAlchemy.

## Vue d’ensemble

Les modèles couvrent les comptes et organisations, les données RH et leurs synchronisations, les réunions et analyses, l’onboarding, ainsi que la gouvernance et l’audit.

Relations principales :

- Une organisation regroupe des utilisateurs et porte les données de réunion, d’onboarding, d’intégration et de gouvernance.
- Un employé peut référencer un autre employé comme manager. Il est associé aux absences, identifiants externes, plans d’onboarding, signaux et scores de risque.
- Un plan d’onboarding contient des tâches ; la suppression ORM du plan entraîne la suppression de ses tâches.
- Les sessions d’authentification appartiennent à un utilisateur.
- Les tables de gouvernance et d’intégration référencent généralement l’organisation ; les clés étrangères `ondelete` sont précisées dans les sections ci-dessous.

## Comptes et organisations

### `organizations` — `Organization`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `name` | String(255) | Non | |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |

### `users` — `User`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `email` | String(255) | Non | Unique |
| `password_hash` | String(255) | Non | |
| `org_id` | Integer | Non | FK → `organizations.id` |
| `role` | String(50) | Oui | Défaut : `admin` |
| `created_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

### `auth_sessions` — `AuthSession`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | String(36) | Non | Clé primaire |
| `user_id` | Integer | Non | FK → `users.id`, `ON DELETE CASCADE`, indexé |
| `created_at` | DateTime(timezone=True) | Non | Défaut : date/heure UTC |
| `revoked_at` | DateTime(timezone=True) | Oui | |

## Employés et données RH

### `employees` — `Employee`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Oui | Pas de FK déclarée dans ce modèle |
| `external_id` | String(100) | Oui | |
| `name` | String(255) | Non | |
| `role` | String(100) | Oui | |
| `team` | String(100) | Oui | |
| `manager_id` | Integer | Oui | FK → `employees.id` |
| `hire_date` | Date | Oui | |
| `status` | String(50) | Oui | Défaut : `active` |
| `data_contract_version` | String(20) | Non | Défaut : `hr.v1` |
| `created_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

### `employee_absences` — `EmployeeAbsence`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `employee_id` | Integer | Non | FK → `employees.id`, `ON DELETE CASCADE`, indexé |
| `source_key` | String(40) | Non | Identifiant de la source |
| `external_id` | String(120) | Non | Identifiant de l’absence dans la source |
| `start_date` | Date | Non | |
| `end_date` | Date | Non | Doit être ≥ `start_date` |
| `status` | String(20) | Non | Défaut : `unknown`; valeurs autorisées : `approved`, `pending`, `rejected`, `cancelled`, `unknown` |
| `duration_days` | Float | Oui | |
| `is_half_day` | Boolean | Oui | |
| `contract_version` | String(20) | Non | Défaut : `hr.v1` |
| `source_updated_at` | DateTime | Oui | |
| `synced_at` | DateTime | Non | Défaut : `datetime.utcnow` |

Contraintes : unicité de `(org_id, source_key, external_id)`.

### `employee_external_ids` — `EmployeeExternalId`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `integration_key` | String(40) | Non | |
| `external_id` | String(120) | Non | |
| `employee_id` | Integer | Non | FK → `employees.id`, `ON DELETE CASCADE`, indexé |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contraintes : unicité de `(org_id, integration_key, external_id)` et de `(employee_id, integration_key)`.

## Intégrations

### `integration_credentials` — `IntegrationCredential`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `integration_key` | String(40) | Non | |
| `encrypted_credentials` | Text | Non | Identifiants chiffrés |
| `updated_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `connection_status` | String(20) | Non | Défaut : `unverified` |
| `connection_checked_at` | DateTime | Oui | |
| `connection_error` | String(500) | Oui | |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contrainte : unicité de `(org_id, integration_key)`.

### `integration_syncs` — `IntegrationSync`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `integration_key` | String(40) | Non | |
| `status` | String(20) | Non | Défaut : `idle`; valeurs : `idle`, `queued`, `running`, `succeeded`, `failed` |
| `task_id` | String(120) | Oui | |
| `records_synced` | Integer | Non | Défaut : `0` |
| `absence_records_synced` | Integer | Non | Défaut : `0` |
| `data_coverage` | JSON | Oui | |
| `last_started_at` | DateTime | Oui | |
| `last_success_at` | DateTime | Oui | |
| `last_error` | String(500) | Oui | |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contrainte : unicité de `(org_id, integration_key)`.

## Réunions et analyses

### `meetings` — `Meeting`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id` |
| `source` | String(50) | Oui | |
| `title` | String(255) | Oui | |
| `participants` | JSON | Oui | |
| `transcript` | Text | Oui | |
| `decisions` | JSON | Oui | |
| `action_items` | JSON | Oui | |
| `risk_signals` | JSON | Oui | |
| `analyzed_at` | DateTime | Oui | |
| `reviewed_at` | DateTime | Oui | |
| `reviewed_by` | Integer | Oui | Pas de FK déclarée dans ce modèle |
| `created_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

### `signals` — `Signal`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `employee_id` | Integer | Non | FK → `employees.id` |
| `source` | String(50) | Non | |
| `type` | String(100) | Non | |
| `value` | Float | Oui | |
| `weight` | Float | Oui | Défaut : `1.0` |
| `captured_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

### `risk_scores` — `RiskScore`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `employee_id` | Integer | Non | FK → `employees.id` |
| `score` | Float | Non | |
| `level` | String(20) | Non | |
| `confidence` | Float | Non | |
| `drivers` | JSON | Oui | |
| `canonical_context` | JSON | Oui | |
| `recommendation` | String(100) | Oui | |
| `created_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

### `insights` — `Insight`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Oui | Pas de FK déclarée dans ce modèle |
| `type` | String(50) | Non | |
| `title` | String(255) | Non | |
| `description` | Text | Oui | |
| `data` | JSON | Oui | |
| `severity` | String(20) | Oui | Défaut : `info` |
| `created_at` | DateTime | Oui | Défaut : `datetime.utcnow` |

## Onboarding

### `onboarding_plans` — `OnboardingPlan`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, indexé |
| `employee_id` | Integer | Non | FK → `employees.id`, indexé |
| `start_date` | Date | Non | |
| `status` | String(20) | Non | Défaut : `active` |
| `created_by` | Integer | Oui | FK → `users.id` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |
| `completed_at` | DateTime | Oui | |

La relation ORM `tasks` trie les tâches par `sort_order`, puis `id`, et supprime les tâches orphelines avec le plan.

### `onboarding_tasks` — `OnboardingTask`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `plan_id` | Integer | Non | FK → `onboarding_plans.id`, `ON DELETE CASCADE`, indexé |
| `title` | String(160) | Non | |
| `description` | String(1000) | Oui | |
| `category` | String(50) | Non | Défaut : `general` |
| `owner_type` | String(20) | Non | Défaut : `manager` |
| `due_date` | Date | Non | |
| `status` | String(20) | Non | Défaut : `pending` |
| `sort_order` | Integer | Non | Défaut : `0` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `completed_at` | DateTime | Oui | |

## Gouvernance et audit

### `audit_events` — `AuditEvent`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | BigInteger (Integer sous SQLite) | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE` |
| `actor_user_id` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `event_type` | String(100) | Non | |
| `resource_type` | String(80) | Non | |
| `resource_id` | String(120) | Oui | |
| `outcome` | String(20) | Non | Défaut : `success` |
| `details` | JSON | Non | Défaut : dictionnaire vide |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow`, indexé |

Index composite `(org_id, created_at, id)`. Les événements sont append-only : les modifications et suppressions ORM déclenchent une erreur.

### `organization_secrets` — `OrganizationSecret`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `name` | String(100) | Non | |
| `description` | String(255) | Oui | |
| `encrypted_value` | Text | Non | Valeur chiffrée |
| `version` | Integer | Non | Défaut : `1` |
| `created_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `updated_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contrainte : unicité de `(org_id, name)`.

### `governance_access_rules` — `AccessRule`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `role` | String(50) | Non | |
| `resource` | String(100) | Non | |
| `action` | String(50) | Non | |
| `effect` | String(10) | Non | |
| `enabled` | Boolean | Non | Défaut : `True` |
| `created_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contrainte : unicité de `(org_id, role, resource, action)`.

### `governance_budget_policies` — `BudgetPolicy`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `name` | String(100) | Non | |
| `meter` | String(80) | Non | Défaut : `llm_cost` |
| `unit` | String(20) | Non | Défaut : `EUR` |
| `limit_amount` | Numeric(14, 6) | Non | Doit être > 0 |
| `period` | String(12) | Non | Défaut : `monthly`; valeurs : `daily`, `monthly` |
| `scope` | String(20) | Non | Défaut : `organization`; valeurs : `organization`, `model`, `user` |
| `scope_key` | String(120) | Oui | |
| `hard_limit` | Boolean | Non | Défaut : `True` |
| `enabled` | Boolean | Non | Défaut : `True` |
| `created_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contraintes : unicité de `(org_id, name)` ; contraintes de validation sur `period`, `scope` et `limit_amount`.

### `governance_budget_usage` — `BudgetUsage`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | BigInteger (Integer sous SQLite) | Non | Clé primaire |
| `policy_id` | Integer | Non | FK → `governance_budget_policies.id`, `ON DELETE CASCADE` |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `amount` | Numeric(14, 6) | Non | |
| `occurred_at` | DateTime | Non | Défaut : `datetime.utcnow`, indexé |
| `source` | String(80) | Non | Défaut : `application` |
| `idempotency_key` | String(120) | Oui | |
| `metadata_json` | JSON | Non | Défaut : dictionnaire vide |

Index composite `(policy_id, occurred_at)` et unicité de `(policy_id, idempotency_key)`.

### `governance_controls` — `GovernanceControl`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `control_key` | String(80) | Non | |
| `enabled` | Boolean | Non | Défaut : `True` |
| `mode` | String(10) | Non | Défaut : `block`; valeurs : `monitor`, `block` |
| `settings` | JSON | Non | Défaut : dictionnaire vide |
| `updated_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contraintes : unicité de `(org_id, control_key)` ; `mode` limité à `monitor` ou `block`.

### `governance_approval_requests` — `ApprovalRequest`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `action_key` | String(100) | Non | |
| `resource_type` | String(80) | Non | |
| `resource_id` | String(120) | Oui | |
| `requested_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `status` | String(12) | Non | Défaut : `pending` |
| `reviewed_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `reviewer_note` | String(500) | Oui | |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `reviewed_at` | DateTime | Oui | |

Index composite `(org_id, status, created_at)`.

### `governance_retention_policies` — `RetentionPolicy`

| Colonne | Type | NULL | Détails |
|---|---|---:|---|
| `id` | Integer | Non | Clé primaire |
| `org_id` | Integer | Non | FK → `organizations.id`, `ON DELETE CASCADE`, indexé |
| `meetings_days` | Integer | Oui | Durée de conservation des réunions en jours |
| `signals_days` | Integer | Oui | Durée de conservation des signaux en jours |
| `risk_scores_days` | Integer | Oui | Durée de conservation des scores de risque en jours |
| `insights_days` | Integer | Oui | Durée de conservation des insights en jours |
| `completed_onboarding_days` | Integer | Oui | Durée de conservation des onboardings terminés en jours |
| `updated_by` | Integer | Oui | FK → `users.id`, `ON DELETE SET NULL` |
| `created_at` | DateTime | Non | Défaut : `datetime.utcnow` |
| `updated_at` | DateTime | Non | Défaut et mise à jour : `datetime.utcnow` |

Contrainte : une politique au plus par organisation (`org_id` unique).

## Remarques de lecture

- Les colonnes sans contrainte `nullable=False` sont nullable selon la déclaration SQLAlchemy, même si une valeur par défaut est définie.
- Les noms des tables et les références ci-dessus viennent des `__tablename__`, `Column`, `ForeignKey`, contraintes et index présents dans les modèles. Ce document ne décrit pas d’éventuelles migrations ou contraintes ajoutées ailleurs dans le projet.
- Les méthodes `to_dict()` exposent des représentations applicatives de certains modèles ; elles ne modifient pas la structure des tables.
