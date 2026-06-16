state = {
  "system_reserve_tokens": 18837,
  "output_reserve_tokens": 4915,
  "working_budget": 9016,   // ridicule face a system_reserve_tokens, a revoir
  "working_tokens_used": 0,
  "depth_current": 0,
  "search_calls_total": 0,
  "search_calls_since_last_persist": 0, // voir a quoi sert ce truc ou si hallucination
  "reeval_pending": false, // assez mal nomme, à revoir, indique si une réévaluation est en attente suite à une action humaine ou un changement de contexte
  "active_subagent": null,
  "last_iteration_at_utc": "",
  "blocked_subagent_code": null,
  "blocked_subagent_request_id": null,
  "pending_human_answer": null,
  "plan_mode": false, // a remplacer par active_plan_id ou active_plan_id null si pas de plan actif
  "active_plan_id": null, // ou "id1", "id2", etc. si un plan est actif
  "plans": 
  {
    "id1": 
    {
      plan_file_path: "plan_id1.json", // chemin vers le fichier de planification, ou null si pas encore généré
      status: "in_progress", // or "pending", "failed", "blocked" (lien avec pending_human_answer), "completed"
      approved: false, 
      todo_file: "plan_id1_todo.json", // ou null si pas encore généré
      generated_files: 
      ['file1.py', 'file2.md', 'file3.png'], // liste des fichiers générés dans le cadre de ce plan, vivent dans le workspace
    },
    "id2": 
    {
      ["..."]
    },
  }
}

/*

plans[active_plan_id].status : null ou "id1", "id2", etc. si un plan est actif, permet de savoir rapidement quel plan est en cours d'exécution sans devoir parcourir tous les plans


Note: plans pourrait etre un tableau plutôt qu'un objet, à voir selon les besoins de recherche et d'accès aux plans
*/
