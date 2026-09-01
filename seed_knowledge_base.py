"""
One-time: pre-seed the Knowledge Base in Firestore with past resolved incidents,
each with a precomputed Vertex AI embedding.

    python3 seed_knowledge_base.py

Safe to re-run (idempotent -- kb_id is the doc id, .set() overwrites).
Prereqs: ADC configured, Vertex AI enabled, Firestore reachable.
"""

import logging

import knowledge_base

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  %(message)s")

    print(f"seeding '{knowledge_base.KB_COLLECTION}' with {len(knowledge_base.SEED_RECORDS)} "
          f"records (embed model {knowledge_base.EMBED_MODEL})...\n")

    n = knowledge_base.seed()

    print(f"\ndone: {n} records seeded.")
    print("\nverify:")
    for snap in knowledge_base._db().collection(knowledge_base.KB_COLLECTION).stream():
        d = snap.to_dict()
        print(f"  {d['kb_id']:8}  {d['fault_class']:16}  {d['action_taken']:16}  "
              f"{d['outcome']:13}  embedding[{len(d['embedding'])}]")
