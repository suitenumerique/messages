const ALBERT_URL = "https://albert.api.etalab.gouv.fr/v1/chat/completions";
const ALBERT_API_KEY = "sk-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyX2lkIjo4NDQ4LCJ0b2tlbl9pZCI6MTUwNywiZXhwaXJlc19hdCI6MTc4MDQzNzYwMH0.AJd2FyLOwpt2rQX0zzbiTQNTkVpmXXxisJE474l47M8";

export const useAlbert = () => {
    const callAbert = async (prompt: string) => {
        return fetch(ALBERT_URL, {
            method: "POST",
            headers: {
                Authorization: `Bearer ${ALBERT_API_KEY}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                "model": "albert-large",
                "messages" : [{"role": "user", "content": prompt}],
            }),
        })
    }
    const checkMissingAttachments = async (content: string): Promise<boolean> => {
        const prompt = `
        Tu es un classifieur. Ta tâche est de déterminer si le contenu d’un e-mail HTML implique **qu’un document ou fichier est ou devrait être joint** (par exemple : mention d'une pièce jointe, de documents envoyés, d’un fichier, etc.).
        Tu dois répondre de manière **strictement booléenne** :
        - Réponds **uniquement** par "TRUE" (sans ponctuation ni guillemets) **si le contenu laisse penser qu’un fichier est ou devait être joint**.
        - Réponds **uniquement** par "FALSE" dans tous les autres cas.
        Tu ne dois rien expliquer, commenter ou formater d'une autre façon.
        Voici le contenu du mail au format HTML :
        \`\`\`
        ${content}
        \`\`\`
    `


        const response = await callAbert(prompt);

        if (!response.ok) return false;

        const data = await response.json();
        return data.choices[0].message.content === "TRUE";
    }

    return {
        checkMissingAttachments
    }
}
