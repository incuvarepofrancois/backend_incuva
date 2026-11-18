# Import contrôlé des signaux de risque

Ce premier import sert à alimenter la détection avec des données préparées et vérifiables, avant de connecter un outil RH. Il ajoute des lignes à la table `signals` de PostgreSQL INCUVA; il ne se connecte pas à la base de données de l'entreprise.

## Format du CSV

Utiliser le modèle `templates/risk_signals_template.csv` avec ces colonnes :

| Colonne | Contenu |
| --- | --- |
| `employee_id` | Identifiant du collaborateur dans INCUVA, pas son identifiant RH externe |
| `indicator` | Clé présente dans `app/modules/risk_detection/indicators.py` |
| `value` | Intensité déjà normalisée entre `0` et `100`; ce n'est pas la donnée brute |
| `captured_at` | Date ISO 8601 avec fuseau horaire, par exemple `2026-10-14T12:00:00Z` |

Exemple de ligne :

```csv
employee_id,indicator,value,captured_at
12,after_hours_work_increase,42,2026-10-14T12:00:00Z
```

Les poids sont repris du catalogue d'indicateurs du backend. Le calcul utilise les signaux des 90 derniers jours. Les signaux qui ont le même collaborateur, le même indicateur, la même valeur et la même date ne sont pas importés deux fois. Le fichier doit utiliser des indicateurs connus et chaque collaborateur doit appartenir à l'organisation passée en option. Les fichiers séparés par des virgules, points-virgules ou tabulations sont acceptés.

## Prévisualiser puis importer

Depuis `backend/`, avec l'environnement Python activé et les variables `DATABASE_URL` et `JWT_SECRET_KEY` chargées :

```powershell
$env:FLASK_APP = "run.py"
$env:FLASK_ENV = "development"
flask import-risk-signals .\templates\risk_signals_template.csv --org-id 1
flask import-risk-signals .\templates\risk_signals_template.csv --org-id 1 --commit
```

La première commande valide le fichier sans écrire en base. La seconde écrit les lignes uniquement si tout le fichier est valide; en cas d'erreur, aucune ligne n'est ajoutée. Remplacer `1` par l'identifiant réel de l'organisation.

Après l'import, lancer **Analyser** dans la page Risques pour recalculer le résultat à partir des signaux disponibles. Les résultats sont enregistrés et affichés au rechargement.

Les scores et poids du prototype doivent être calibrés et validés avant tout usage opérationnel. N'importer que des données autorisées et explicables; le résultat doit rester une aide à l'examen humain, jamais une décision automatisée concernant un collaborateur.
