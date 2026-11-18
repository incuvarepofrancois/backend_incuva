from datetime import timedelta


ROLE_TASKS = (
    (("engineer", "developer", "développeur", "développeuse", "tech"), {
        "title": "Découvrir l’environnement de travail",
        "description": "Présenter les outils, les pratiques et les interlocuteurs utiles au poste.",
        "category": "role",
        "day": 7,
    }),
    (("sales", "commercial", "commerciale", "account executive"), {
        "title": "Découvrir le cycle commercial",
        "description": "Présenter les étapes du cycle, les outils et les ressources de l’équipe.",
        "category": "role",
        "day": 7,
    }),
    (("manager", "lead", "responsable", "directeur", "directrice"), {
        "title": "Cartographier les interlocuteurs clés",
        "description": "Identifier les partenaires, rituels et décisions liés au périmètre du poste.",
        "category": "role",
        "day": 7,
    }),
)


BASE_TASKS = (
    {
        "title": "Préparer les accès et le matériel",
        "description": "Vérifier que les accès, équipements et informations pratiques sont prêts.",
        "category": "preparation",
        "owner_type": "hr",
        "day": -1,
    },
    {
        "title": "Présenter le rôle et les repères de l’équipe",
        "description": "Partager les missions, les objectifs de départ et les personnes à contacter.",
        "category": "welcome",
        "owner_type": "manager",
        "day": 0,
    },
    {
        "title": "Organiser les premières rencontres",
        "description": "Planifier les échanges nécessaires avec l’équipe et les partenaires proches.",
        "category": "team",
        "owner_type": "manager",
        "day": 3,
    },
    {
        "title": "Faire un point de prise en main",
        "description": "Recueillir les questions et lever les premiers blocages.",
        "category": "check_in",
        "owner_type": "manager",
        "day": 7,
    },
    {
        "title": "Vérifier les besoins de support",
        "description": "Ajuster les ressources, les rencontres ou les priorités si nécessaire.",
        "category": "check_in",
        "owner_type": "employee",
        "day": 14,
    },
    {
        "title": "Faire le bilan du premier mois",
        "description": "Revoir les objectifs de départ et convenir des prochaines étapes.",
        "category": "check_in",
        "owner_type": "manager",
        "day": 30,
    },
)


class OnboardingPlanner:
    """Build a transparent, editable starter plan from role and start date."""

    def build_tasks(self, role, start_date):
        tasks = [dict(task) for task in BASE_TASKS]
        normalized_role = (role or "").casefold()
        role_task = next(
            (task for keywords, task in ROLE_TASKS if any(keyword in normalized_role for keyword in keywords)),
            None,
        )
        if role_task:
            tasks.append({**role_task, "owner_type": "manager"})

        tasks.sort(key=lambda task: task["day"])
        for index, task in enumerate(tasks):
            task["due_date"] = start_date + timedelta(days=task.pop("day"))
            task["sort_order"] = index
        return tasks
