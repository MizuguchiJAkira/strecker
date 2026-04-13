"""Individual deer re-identification module.

Assigns persistent individual IDs to deer detected across trail cam photos
using visual embedding similarity. Think "Pokémon Dex for your deer."

Architecture:
    1. Crop — MegaDetector bbox → tight deer crop (already available)
    2. Embed — backbone encoder maps crop → 128-d feature vector
    3. Match — cosine similarity against known individuals in FAISS index
    4. Confirm — user confirms/merges/splits via dashboard UI

Phase 1 (MVP):
    - Buck re-ID via antler geometry (within single season)
    - Embedding model: MegaDescriptor-L (Nguyen et al. 2024) or
      fine-tuned DINOv2-ViT-S (Oquab et al. 2024)
    - FAISS flat index (sufficient up to ~500 individuals per property)
    - Human-in-the-loop confirmation flow

Phase 2:
    - Doe re-ID via body/face features (harder, lower confidence)
    - Cross-season matching with antler growth trajectory priors
    - Age class estimation (spike → 2.5yr → 3.5yr → mature)

Phase 3:
    - Boone & Crockett score estimation from multi-angle photos
    - Population census with mark-recapture statistics
    - "New deer alert" push notifications

References:
    - Schneider et al. 2020 — "Similarity learning for wildlife re-ID"
    - Nguyen et al. 2024 — MegaDescriptor: large-scale wildlife re-ID
    - Li et al. 2019 — ATRW: tiger re-ID (analogous antler problem)
    - Nepovinnykh et al. 2022 — SealID: metric learning for pinnipeds
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DeerEmbedding:
    """Feature vector extracted from a single deer crop."""
    image_filename: str
    camera_id: str
    timestamp: datetime
    species_key: str  # white_tailed_deer, axis_deer
    embedding: np.ndarray  # shape (128,)
    bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)  # x1, y1, x2, y2
    antler_classification: Optional[str] = None  # buck / doe / unknown
    crop_quality_score: float = 0.0  # 0-1, based on sharpness + size


@dataclass
class Individual:
    """A recognized individual deer."""
    individual_id: str  # e.g. "DEER-a3f8c2"
    property_id: int
    species_key: str
    display_name: Optional[str] = None  # user-assigned, e.g. "Split G2"
    sex: Optional[str] = None  # buck / doe / unknown
    age_class: Optional[str] = None  # spike, 2.5yr, 3.5yr, 4.5yr+, unknown
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    sighting_count: int = 0
    camera_ids: List[str] = field(default_factory=list)
    # Centroid embedding — running average of all confirmed sightings
    centroid_embedding: Optional[np.ndarray] = None
    # Best photo (highest quality score) for profile display
    profile_photo_url: Optional[str] = None
    notes: Optional[str] = None
    is_confirmed: bool = False  # user has confirmed at least one match


@dataclass
class Sighting:
    """A single observation of a known individual."""
    individual_id: str
    image_filename: str
    camera_id: str
    timestamp: datetime
    confidence: float  # re-ID confidence (cosine similarity)
    embedding: Optional[np.ndarray] = None
    is_confirmed: bool = False  # user-confirmed (vs auto-matched)


# ═══════════════════════════════════════════════════════════════════════════
# Encoder — extracts feature vectors from deer crops
# ═══════════════════════════════════════════════════════════════════════════

class DeerEncoder:
    """Encodes deer crops into 128-d feature vectors.

    Production: MegaDescriptor-L or fine-tuned DINOv2-ViT-S/14
    Demo: deterministic pseudo-embeddings from image filename hash
    """

    def __init__(self, model_path: Optional[str] = None, demo: bool = False):
        self.demo = demo
        self.model = None
        self.device = "cpu"

        if not demo and model_path:
            self._load_model(model_path)

    def _load_model(self, model_path: str) -> None:
        """Load the re-ID encoder backbone.

        Expected: torchvision-compatible model that outputs (batch, 128) embeddings.
        Training: metric learning with triplet loss + hard negative mining.
        """
        try:
            import torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = torch.load(model_path, map_location=self.device)
            self.model.eval()
            logger.info(f"Re-ID encoder loaded from {model_path} on {self.device}")
        except Exception as e:
            logger.warning(f"Could not load re-ID model: {e}. Falling back to demo mode.")
            self.demo = True

    def encode(self, crop_path: str, bbox: Tuple[int, int, int, int] = None) -> np.ndarray:
        """Extract 128-d embedding from a deer crop.

        Args:
            crop_path: Path to cropped deer image (or full image if bbox provided)
            bbox: Optional bounding box (x1, y1, x2, y2) to crop from full image

        Returns:
            Normalized 128-d float32 vector (unit L2 norm)
        """
        if self.demo:
            return self._demo_embedding(crop_path)

        return self._model_embedding(crop_path, bbox)

    def _model_embedding(self, crop_path: str, bbox=None) -> np.ndarray:
        """Production embedding via trained encoder."""
        import torch
        from PIL import Image
        from torchvision import transforms

        img = Image.open(crop_path).convert("RGB")
        if bbox:
            img = img.crop(bbox)

        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225]),
        ])

        tensor = transform(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            emb = self.model(tensor).cpu().numpy().flatten()

        # L2 normalize
        norm = np.linalg.norm(emb)
        return (emb / norm).astype(np.float32) if norm > 0 else emb.astype(np.float32)

    # Pre-generated pool of demo deer names (assigned during matching)
    DEMO_DEER_NAMES = [
        "Old Mossy", "Split G2", "Wide 8", "Drop Tine", "Kicker",
        "Tall Boy", "Double Beam", "Sticker", "Club Rack", "Ghost",
        "Big Mama", "Creek Doe", "Twin Doe", "Notch Ear", "Blaze",
        "Shadow", "Lone Star", "Mesquite", "Rio", "Cedar",
        "Brushy", "Tank", "Dusty", "Ranger", "Scout",
        "Bandit", "Copper", "Flicker", "Monarch", "Patches",
        "Ridgeback", "Sable", "Tracker", "Whiskey", "Bramble",
        "Cactus", "Dagger", "Ember", "Forked", "Granite",
        "Halo", "Ironwood", "Juniper", "Knob", "Limestone",
        "Maverick", "Nightshade", "Oakley", "Paladin", "Quartz",
    ]

    def _demo_embedding(self, crop_path: str) -> np.ndarray:
        """Deterministic pseudo-embedding for demo mode.

        Simulates a realistic population of ~20-25 individuals by assigning
        each photo to one of a fixed pool of deer based on a hash of the
        filename. Photos from the same camera on the same day get the same
        individual (burst consistency), and overall distribution follows a
        realistic pattern where some deer are seen much more often than others.
        """
        fname = Path(crop_path).stem  # e.g. "CAM-F01_20250303_184639_00"
        parts = fname.split("_")

        # Assign to one of ~22 individuals using camera+date hash mod pool size
        # This ensures same camera + same day = same deer (burst consistency)
        if len(parts) >= 3:
            identity_key = "_".join(parts[:2])  # CAM-F01_20250303
        else:
            identity_key = fname

        pool_size = 22  # ~22 individual deer on this property
        identity_idx = int(hashlib.md5(identity_key.encode()).hexdigest()[:8], 16) % pool_size

        # Deterministic 128-d vector per individual
        rng = np.random.RandomState(identity_idx * 7919)  # prime seed per individual
        emb = rng.randn(128).astype(np.float32)

        # Add small per-photo noise so burst photos are similar but not identical
        photo_seed = int(hashlib.md5(fname.encode()).hexdigest()[:8], 16)
        photo_rng = np.random.RandomState(photo_seed)
        noise = photo_rng.randn(128).astype(np.float32) * 0.05
        emb += noise

        # L2 normalize
        norm = np.linalg.norm(emb)
        return (emb / norm).astype(np.float32) if norm > 0 else emb

    def batch_encode(self, crop_paths: List[str]) -> List[np.ndarray]:
        """Encode multiple crops. Production mode uses batch inference."""
        if self.demo:
            return [self.encode(p) for p in crop_paths]

        # TODO: batch GPU inference for production throughput
        return [self.encode(p) for p in crop_paths]


# ═══════════════════════════════════════════════════════════════════════════
# Matcher — finds matching individuals for new embeddings
# ═══════════════════════════════════════════════════════════════════════════

class DeerMatcher:
    """Matches deer embeddings against known individuals.

    Uses cosine similarity with configurable thresholds:
      - MATCH_THRESHOLD (0.75): above this = auto-match
      - CANDIDATE_THRESHOLD (0.55): above this = candidate for user review
      - Below candidate threshold = new individual

    Spatial + temporal priors narrow the search:
      - Same camera within 2 hours → boost similarity by 0.05
      - Same property cameras → no penalty
      - Different property → never match (individuals are per-property)
    """

    def __init__(self):
        self.individuals: Dict[str, Individual] = {}
        self.sightings: List[Sighting] = []
        self._index = None  # FAISS index (built lazily)

    def match(
        self,
        embedding: DeerEmbedding,
        property_id: int,
        top_k: int = 5,
    ) -> List[Tuple[str, float]]:
        """Find top-k matching individuals for a new embedding.

        Args:
            embedding: DeerEmbedding from encoder
            property_id: restrict matches to this property
            top_k: number of candidates to return

        Returns:
            List of (individual_id, similarity_score) tuples, sorted descending
        """
        property_individuals = {
            iid: ind for iid, ind in self.individuals.items()
            if ind.property_id == property_id
            and ind.species_key == embedding.species_key
        }

        if not property_individuals:
            return []

        results = []
        for iid, ind in property_individuals.items():
            if ind.centroid_embedding is None:
                continue
            sim = self._cosine_similarity(embedding.embedding, ind.centroid_embedding)

            # Spatial-temporal boost: same camera within 2 hours
            if embedding.camera_id in ind.camera_ids and ind.last_seen:
                time_diff = abs((embedding.timestamp - ind.last_seen).total_seconds())
                if time_diff < 7200:  # 2 hours
                    sim += settings.REID_TEMPORAL_BOOST

            results.append((iid, float(min(sim, 1.0))))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def register_individual(
        self,
        embedding: DeerEmbedding,
        property_id: int,
    ) -> Individual:
        """Create a new individual from an unmatched embedding."""
        short_hash = hashlib.md5(
            f"{property_id}:{embedding.image_filename}:{embedding.timestamp}".encode()
        ).hexdigest()[:6]

        individual = Individual(
            individual_id=f"DEER-{short_hash}",
            property_id=property_id,
            species_key=embedding.species_key,
            sex=embedding.antler_classification,
            first_seen=embedding.timestamp,
            last_seen=embedding.timestamp,
            sighting_count=1,
            camera_ids=[embedding.camera_id],
            centroid_embedding=embedding.embedding.copy(),
            profile_photo_url=f"/photos/{embedding.species_key}/{embedding.image_filename}",
        )

        self.individuals[individual.individual_id] = individual

        sighting = Sighting(
            individual_id=individual.individual_id,
            image_filename=embedding.image_filename,
            camera_id=embedding.camera_id,
            timestamp=embedding.timestamp,
            confidence=1.0,  # first sighting is definitionally correct
            embedding=embedding.embedding,
        )
        self.sightings.append(sighting)

        return individual

    def add_sighting(
        self,
        individual_id: str,
        embedding: DeerEmbedding,
        confidence: float,
    ) -> Sighting:
        """Record a new sighting of a known individual and update centroid."""
        ind = self.individuals[individual_id]
        ind.sighting_count += 1
        ind.last_seen = embedding.timestamp
        if embedding.camera_id not in ind.camera_ids:
            ind.camera_ids.append(embedding.camera_id)

        # Running average centroid update
        n = ind.sighting_count
        ind.centroid_embedding = (
            (ind.centroid_embedding * (n - 1) + embedding.embedding) / n
        )
        # Re-normalize
        norm = np.linalg.norm(ind.centroid_embedding)
        if norm > 0:
            ind.centroid_embedding /= norm

        # Update profile photo if this crop is better quality
        sighting = Sighting(
            individual_id=individual_id,
            image_filename=embedding.image_filename,
            camera_id=embedding.camera_id,
            timestamp=embedding.timestamp,
            confidence=confidence,
            embedding=embedding.embedding,
        )
        self.sightings.append(sighting)

        return sighting

    def merge_individuals(self, keep_id: str, merge_id: str) -> None:
        """Merge two individuals (user says they're the same deer).

        Keeps the first, absorbs all sightings from the second,
        recomputes centroid embedding.
        """
        keep = self.individuals[keep_id]
        merge = self.individuals[merge_id]

        # Transfer sightings
        for s in self.sightings:
            if s.individual_id == merge_id:
                s.individual_id = keep_id

        keep.sighting_count += merge.sighting_count
        keep.first_seen = min(keep.first_seen, merge.first_seen)
        keep.last_seen = max(keep.last_seen, merge.last_seen)
        keep.camera_ids = list(set(keep.camera_ids + merge.camera_ids))

        # Recompute centroid from all sightings
        embeddings = [
            s.embedding for s in self.sightings
            if s.individual_id == keep_id and s.embedding is not None
        ]
        if embeddings:
            centroid = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(centroid)
            keep.centroid_embedding = centroid / norm if norm > 0 else centroid

        del self.individuals[merge_id]

    def split_sighting(self, sighting_idx: int) -> Individual:
        """Split a sighting into a new individual (user says wrong match)."""
        sighting = self.sightings[sighting_idx]
        old_id = sighting.individual_id

        # Create new individual from this sighting
        short_hash = hashlib.md5(
            f"split:{sighting.image_filename}:{datetime.utcnow()}".encode()
        ).hexdigest()[:6]

        new_individual = Individual(
            individual_id=f"DEER-{short_hash}",
            property_id=self.individuals[old_id].property_id,
            species_key=self.individuals[old_id].species_key,
            first_seen=sighting.timestamp,
            last_seen=sighting.timestamp,
            sighting_count=1,
            camera_ids=[sighting.camera_id],
            centroid_embedding=sighting.embedding.copy() if sighting.embedding is not None else None,
            profile_photo_url=f"/photos/{self.individuals[old_id].species_key}/{sighting.image_filename}",
        )

        sighting.individual_id = new_individual.individual_id
        self.individuals[new_individual.individual_id] = new_individual

        # Recompute old individual's centroid
        old_ind = self.individuals[old_id]
        old_ind.sighting_count -= 1
        remaining = [
            s.embedding for s in self.sightings
            if s.individual_id == old_id and s.embedding is not None
        ]
        if remaining:
            centroid = np.mean(remaining, axis=0)
            norm = np.linalg.norm(centroid)
            old_ind.centroid_embedding = centroid / norm if norm > 0 else centroid

        return new_individual

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two L2-normalized vectors."""
        return float(np.dot(a, b))


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline — orchestrates the full re-ID workflow
# ═══════════════════════════════════════════════════════════════════════════

def run_reid_pipeline(
    photo_dir: Path,
    property_id: int,
    species_keys: List[str] = None,
    demo: bool = False,
) -> Tuple[List[Individual], List[Sighting]]:
    """Run the full re-ID pipeline on a directory of sorted photos.

    Args:
        photo_dir: Path to sorted species subdirectories (e.g. demo/output/sorted/)
        property_id: Property these photos belong to
        species_keys: Which species to process (default: deer species only)
        demo: Use demo encoder (deterministic pseudo-embeddings)

    Returns:
        Tuple of (individuals, sightings) discovered
    """
    if species_keys is None:
        species_keys = settings.REID_ENABLED_SPECIES

    encoder = DeerEncoder(
        model_path=settings.REID_MODEL_PATH if not demo else None,
        demo=demo,
    )
    matcher = DeerMatcher()

    total_photos = 0
    total_individuals = 0
    total_sightings = 0

    for species_key in species_keys:
        species_dir = photo_dir / species_key
        if not species_dir.exists():
            continue

        photos = sorted(species_dir.glob("*.jpg"))
        logger.info(f"Re-ID: processing {len(photos)} photos for {species_key}")

        for photo_path in photos:
            # Extract embedding
            emb_vector = encoder.encode(str(photo_path))

            # Parse metadata from filename
            import re
            m = re.match(
                r"^(CAM-\w+)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})_(\d+)\.jpg$",
                photo_path.name, re.IGNORECASE,
            )
            if not m:
                continue

            camera_id = m.group(1)
            ts = datetime(
                int(m.group(2)), int(m.group(3)), int(m.group(4)),
                int(m.group(5)), int(m.group(6)), int(m.group(7)),
            )

            deer_emb = DeerEmbedding(
                image_filename=photo_path.name,
                camera_id=camera_id,
                timestamp=ts,
                species_key=species_key,
                embedding=emb_vector,
            )

            # Try to match against known individuals
            matches = matcher.match(deer_emb, property_id)
            total_photos += 1

            if matches and matches[0][1] >= settings.REID_MATCH_THRESHOLD:
                # Auto-match
                best_id, best_sim = matches[0]
                matcher.add_sighting(best_id, deer_emb, best_sim)
                total_sightings += 1
            elif matches and matches[0][1] >= settings.REID_CANDIDATE_THRESHOLD:
                # Candidate — auto-match for now, user can split later
                best_id, best_sim = matches[0]
                matcher.add_sighting(best_id, deer_emb, best_sim)
                total_sightings += 1
            else:
                # New individual
                matcher.register_individual(deer_emb, property_id)
                total_individuals += 1
                total_sightings += 1

    # ── Demo post-processing: assign names and sex ──────────────────────
    if demo:
        all_individuals = list(matcher.individuals.values())

        # Group by species so names don't collide across whitetail / axis
        from collections import defaultdict
        by_species = defaultdict(list)
        for ind in all_individuals:
            by_species[ind.species_key].append(ind)

        names = DeerEncoder.DEMO_DEER_NAMES
        name_cursor = 0  # global cursor so each species gets different names

        for species_key, group in by_species.items():
            # Sort by sighting count descending so the most-seen deer get
            # the "best" names at the top of the list
            group.sort(key=lambda i: i.sighting_count, reverse=True)

            for idx, ind in enumerate(group):
                # Assign a unique display name from the pre-built list
                ind.display_name = names[name_cursor % len(names)]
                name_cursor += 1

                # Assign sex: first ~60% bucks, next ~30% does, rest unknown
                frac = idx / max(len(group), 1)
                if frac < 0.6:
                    ind.sex = "buck"
                elif frac < 0.9:
                    ind.sex = "doe"
                else:
                    ind.sex = "unknown"

                # Simple age class based on sighting count
                if ind.sighting_count >= 8:
                    ind.age_class = "4.5yr+"
                elif ind.sighting_count >= 5:
                    ind.age_class = "3.5yr"
                elif ind.sighting_count >= 3:
                    ind.age_class = "2.5yr"
                else:
                    ind.age_class = "unknown"

    logger.info(
        f"Re-ID complete: {total_photos} photos → "
        f"{total_individuals} individuals, {total_sightings} sightings"
    )

    return list(matcher.individuals.values()), matcher.sightings
