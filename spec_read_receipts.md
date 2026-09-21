# Read receipts for mentions

## Problème

Quand l'auteur d'un commentaire interne (`ThreadEvent` type `im`) mentionne un collègue avec `@nom`, il ne peut pas savoir si ce dernier a lu la mention.

## Solution

Ajouter un champ `mention_read_by` à l'API `GET /threads/{id}/events/` listant les utilisateurs mentionnés qui ont `read_at IS NOT NULL` (i.e. ont fait scroller le commentaire dans leur vue). L'afficher dans l'UI sous la bulle du commentaire, uniquement pour l'auteur.

---

## Étapes

### Étape 1 — Backend : exposer `mention_read_by`

**Fichier** : `src/backend/core/api/serializers.py`

**Changements** :

1.1 — Nouveau sérialiseur `MentionReadByUserSerializer` (avant `ThreadEventSerializer`)

1.2 — Ajouter `mention_read_by = SerializerMethodField()` dans `ThreadEventSerializer`, l'ajouter à `fields`, et implémenter `get_mention_read_by`.

Comportement :
- `null` si l'utilisateur courant n'est PAS l'auteur
- `[]` si l'utilisateur est l'auteur mais personne n'a encore lu
- `[{id, name, read_at}, ...]` pour les mentionnés qui ont `read_at IS NOT NULL`

### Étape 2 — Régénérer le client API

```bash
make api-schema && make generate-api-client
```

### Étape 3 — Frontend : indicateur visuel

**Fichiers** :

- `src/frontend/src/features/layouts/components/thread-view/components/thread-event/index.tsx`
- `src/frontend/src/features/layouts/components/thread-view/components/thread-event/_index.scss`

Ajouter l'indicateur "Lu par X/Y" avec tooltip dans la bulle du commentaire, visible uniquement par l'auteur.

---

## Revue de cohérence

- Auteur, personne n'a lu → `mention_read_by: []` → rien affiché
- Auteur, X mentionnés ont lu → "Lu par X/Y" + tooltip
- Non-auteur → `mention_read_by: null` → rien affiché
- Édition du commentaire → `sync_im_mentions` synchronise, mention_read_by reflète les mentions actuelles
- Perte d'accès → `_cleanup_invalid_mentions` supprime le UserEvent, l'utilisateur disparaît
