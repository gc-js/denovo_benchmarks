import json
import os
import ppx 
import re
import shutil
import subprocess
import pandas as pd
import alphatims.bruker
from tqdm import tqdm
from pyteomics import fasta, mgf
from oktoberfest.runner import run_job # TODO: remove?

VSC_DATA = "/data/antwerpen/209/vsc20960/"
VSC_SCRATCH = "/scratch/antwerpen/209/vsc20960/"
ROOT = os.path.join(VSC_SCRATCH, "benchmarking")
VSC_FRAGPIPE = "/apps/antwerpen/testing/3276_FragPipe/software/breniac-skylake-rocky8/"

# Path to ThermoRawFileParser apptainer container
RAW_FILE_PARSER_PATH = os.path.join(VSC_SCRATCH, "benchmarking", "thermorawfileparser_latest.sif")
# Path to msconvert apptainer container
MSCONVERT_PATH = os.path.join(VSC_SCRATCH, "benchmarking", "pwiz-skyline-i-agree-to-the-vendor-licenses_latest.sif")
# Path to MSFragger executable file (jar)
MSFRAGGER_PATH = os.path.join(VSC_DATA, "easybuild", "build", "MSFragger-4.0", "MSFragger-4.0.jar")
# Path to MSBooster executable file (jar)
MSBOOSTER_PATH = os.path.join(VSC_FRAGPIPE, "MSBooster", "1.2.31-Java-11", "MSBooster-1.2.31.jar")
DIANN_PATH = os.path.join(VSC_FRAGPIPE, "FragPipe/21.1-Java-11/tools/diann/1.8.2_beta_8/linux/diann-1.8.1.8")
KOINA_URL = "https://koina.wilhelmlab.org:443/v2/models/"
MSBOOSTER_BASE_PARAMS = os.path.join(VSC_SCRATCH, "benchmarking", "rescore_params", "msbooster_base.params")
# Spectrum params order for saving labeled mgf files
MGF_KEY_ORDER = ["title", "pepmass", "rtinseconds", "charge", "scans", "seq"]

PROTEOMES_DIR = os.path.join(ROOT, "proteomes")
RESCORE_PARAMS_DIR = os.path.join(ROOT, "rescore_params")

RAW_DATA_DIR = os.path.join(ROOT, "raw")
MZML_DATA_DIR = os.path.join(ROOT, "mzml")
RESCORED_DATA_DIR = os.path.join(ROOT, "rescored")
MGF_DATA_DIR = os.path.join(ROOT, "mgf")

DATASET_STORAGE_DIR = os.path.join(VSC_DATA, "benchmarking", "datasets")

# Spectra smoothing for .d to .mgf conversion with alphatims
CENTROID_WINDOW = 5
MAX_SPECTRA_PER_FILE = 20000


def get_files_list(download_config):
    """
    Select files for dataset based on 
    selection rules defined in the download_config.
    
    TODO.
    """
    dset_id = download_config.dset_id
    dset_dir = os.path.join(RAW_DATA_DIR, dset_id)
    ext = download_config.ext
    proj = ppx.find_project(
        dset_id, 
        local=dset_dir,
    )

    def check_file(file_path):
        for keyword in download_config.keywords:
            if keyword not in file_path:
                return False
        if not file_path.lower().endswith(ext):
            return False
        return True

    files_list = [
        file_path
        for file_path 
        in proj.remote_files() 
        if check_file(file_path)
    ][:download_config.n_files]

    files_list = {
        os.path.basename(file_path)[:-len(ext)]: file_path
        for file_path
        in files_list
    }
    return files_list


def download_files(download_config, files_list):#, unpack_dir=None, raw_ext=None):
    # TODO: now RAW_DATA_DIR can also contain mzml and mgf -- if specified? 
    # (just all the source data for a DSET_ID always is stored at RAW_DATA_DIR?)
    dset_id = download_config.dset_id
    dset_dir = os.path.join(RAW_DATA_DIR, dset_id)
    print(f"Loading dataset {dset_id} to the folder {dset_dir}")
    proj = ppx.find_project(
        dset_id, 
        local=dset_dir,
    )
    print("local files:", proj.local_files())
    
    # select files to download
    # TODO: change: not skipping existing files so far = download all from fnames
    fnames = list(files_list.values())
    if download_config.ext == ".wiff":
        fnames += [fname + ".scan" for fname in fnames]
    proj.download(fnames)
    print("All local files:\n", proj.local_files()) # TODO: remove


def convert_raw(dset_id, files_list, target_dir, target_ext=".mzml"):
    os.makedirs(target_dir, exist_ok=True)    
    
    dset_dir = os.path.join(RAW_DATA_DIR, dset_id)
    raw_file_pathes = [
        os.path.join(dset_dir, file_path)
        for fname, file_path
        in files_list.items()
    ]
    print("Files:\n", raw_file_pathes)
    print(f"Converting to {target_ext}. Storing to {target_dir}")

    for file_path in tqdm(raw_file_pathes):
        cmd = [
            "apptainer",
            "exec",
            "--cleanenv",
            RAW_FILE_PARSER_PATH,
            "ThermoRawFileParser.sh",
            "-i",
            str(file_path),
            "-o",
            target_dir,
        ]
        if target_ext == ".mgf":
            cmd += ["-f", "0"]
        subprocess.run(" ".join(cmd), shell=True, check=True)
    print(os.listdir(target_dir)) # need this?


def convert_wiff(dset_id, files_list, target_dir, target_ext=".mzml"):
    os.makedirs(target_dir, exist_ok=True)

    dset_dir = os.path.join(RAW_DATA_DIR, dset_id)
    raw_file_pathes = {
        fname: os.path.join(dset_dir, file_path)
        for fname, file_path
        in files_list.items()
    }
    print("Files:\n", list(raw_file_pathes.values()))
    print(f"Converting to {target_ext}. Storing to {target_dir}")

    ext_flag = "--mgf" if target_ext == ".mgf" else "--mzML"
    for fname, file_path in tqdm(raw_file_pathes.items()):
        out_fname = fname + target_ext
        cmd = [
            "apptainer",
            "exec",
            "--cleanenv",
            MSCONVERT_PATH,
            "wine msconvert",
            ext_flag,
            "-z",
            "-o",
            target_dir,
            "--outfile",
            out_fname,
            "--filter",
            '"peakPicking vendor msLevel=1-2"',
            file_path
        ]
        subprocess.run(" ".join(cmd), shell=True, check=True)
    print(os.listdir(target_dir)) # need this?


# TODO: remove, mb not needed
def convert_d_to_mgf(dset_name, files_list):# raw_data_dir, bruker_file_name, mgf_data_dir, mgf_file_name):    
    mzml_files_dir = os.path.join(MZML_DATA_DIR, dset_name)
    mgf_files_dir = os.path.join(MGF_DATA_DIR, dset_name)
    
    for fname in files_list:
        bruker_file_name = fname + ".d"
        bruker_d_folder_name = os.path.join(mzml_files_dir, bruker_file_name)
        mgf_file_name = fname + ".mgf"
        print(fname, bruker_d_folder_name, mgf_file_name)

        data = alphatims.bruker.TimsTOF(bruker_d_folder_name)
        data.save_as_spectra(
            mgf_files_dir, 
            mgf_file_name, 
            centroiding_window=CENTROID_WINDOW,
            keep_n_most_abundant_peaks=-1,
        )
    
    
# should we run it only if there is no prepared decoys file? 
def generate_decoys_fasta(db_file):
    db_path = os.path.join(PROTEOMES_DIR, db_file)

    name, ext = db_file.split(".")
    name = name + "_w_decoys"
    db_w_decoys_file = ".".join([name, ext])

    db_w_decoys_path = os.path.join(PROTEOMES_DIR, db_w_decoys_file)
    
    # TODO: check if file at db_w_decoys_path already exists (if yes, skip creation step)
    
    fasta.write_decoy_db(
        db_path, 
        db_w_decoys_path,
        mode='reverse',
        prefix='rev_', # what Percolator expects to see
    )
    return db_w_decoys_path


def run_database_search(dset_name, db_w_decoys_path, db_search_config):
    search_ext = db_search_config.ext
    mzml_files_dir = os.path.join(MZML_DATA_DIR, dset_name)
    mzml_files = [f for f in os.listdir(mzml_files_dir) if os.path.splitext(f)[1].lower() == search_ext]
    mzml_files = [os.path.join(mzml_files_dir, f) for f in mzml_files]
    
    options = [
        "--database_name",
        db_w_decoys_path,
        "--decoy_prefix",
        "rev_",
        "--output_format",
        "pepxml_pin", # .pin outputs for MSBooster
    ]
    if db_search_config.ext == ".d":
        options += ["--write_uncalibrated_mgf", "1"]
    
    # Parse additional search params from config if provided
    for arg in [*db_search_config.search_params.items()]:
        options += list(map(str, arg))
    
    cmd = [
        "java",
        "-Xmx64G",
        "-jar",
        MSFRAGGER_PATH,
        *options,
        *mzml_files,
    ]
    subprocess.run(" ".join(cmd), shell=True, check=True)
    print("DB search results (.pepXML, .pin):\n", os.listdir(mzml_files_dir))

    if search_ext == ".d":
        # Use uncalibrared mzml files to get "proxy" mzml files
        for file_path in mzml_files:
            fname = os.path.splitext(file_path)[0]
            src_fname = fname + "_uncalibrated.mzML"
            dst_fname = fname + ".mzML"
            shutil.copyfile(src=src_fname, dst=dst_fname) # TODO: replace copy to rename (move)
        print("Created mzML files:\n", os.listdir(mzml_files_dir))

        # Use uncalibrared mgf files to get unlabeled mgf files from .d
        mgf_files_dir = os.path.join(MGF_DATA_DIR, dset_name)
        for file_path in mzml_files:
            fname = os.path.splitext(file_path)[0]
            src_fname = fname + "_uncalibrated.mgf"
            dst_fname = os.path.join(mgf_files_dir, os.path.basename(fname + ".mgf"))
            shutil.copyfile(src=src_fname, dst=dst_fname) # TODO: replace copy to move
        print("Created unlabeled mgf files\n", os.listdir(mgf_files_dir))


def get_psm_rescoring_features(dset_name, rescoring_config):
    """Create PSMs rescoring features with MSBooster."""
    mzml_files_dir = os.path.join(MZML_DATA_DIR, dset_name)
    rescored_files_dir = os.path.join(RESCORED_DATA_DIR, dset_name)
 
    # select all the .mzml files from mzml_files_dir (# only select with fnames in files_list?)
    mzml_files = [f for f in os.listdir(mzml_files_dir) if os.path.splitext(f)[1].lower() == ".mzml"]
    mzml_files = [os.path.join(mzml_files_dir, f) for f in mzml_files]
    print(".mzML files available for rescoring:\n", mzml_files)

    # select .pin files with fnames in files_list
    # TODO: check if there are no problems with existing _rescore.pin files
    pin_files = [f for f in os.listdir(mzml_files_dir) if os.path.splitext(f)[1].lower() == ".pin"]
    pin_files = [os.path.join(mzml_files_dir, f) for f in pin_files]
    print(".pin files available for rescoring:\n", pin_files)

    file_prefix = "rescore" # TODO: move outside?

    options = [
        "--DiaNN",
        DIANN_PATH,
        "--KoinaURL",
        KOINA_URL,
        "--editedPin",
        file_prefix,
        "--paramsList",
        MSBOOSTER_BASE_PARAMS,
        "--mzmlDirectory",
        *mzml_files,
        "--pinPepXMLDirectory",
        *pin_files,
        "--outputDirectory",
        rescored_files_dir,
    ]
    # TODO: Parse additional params from config if provided
    for arg in [*rescoring_config.feat_pred_params.items()]:
        options += list(map(str, arg))
    
    cmd = [
        "java",
        "-Xmx64G",
        "-jar",
        MSBOOSTER_PATH,
        *options,
    ]
    print("MSBOOSTER DEBUG:\n")
    print(" ".join(cmd))
    subprocess.run(" ".join(cmd), shell=True, check=True)
    print("Created PSMs features (_rescore.pin):\n", os.listdir(mzml_files_dir))


def run_psm_rescoring(dset_name, rescoring_config, files_list):
    """Run Percolator for PSMs rescoring (using MSBooster features)."""
    # TODO: move outside (to constants?)
    num_threads = 3
    test_fdr = 0.01
    train_fdr = 0.01
    file_prefix = "rescore"

    mzml_files_dir = os.path.join(MZML_DATA_DIR, dset_name)
    rescored_files_dir = os.path.join(RESCORED_DATA_DIR, dset_name)

    # Merge together PSMs features for all the _rescore.pin files in files_list
    dfs = [
        pd.read_csv(
            os.path.join(mzml_files_dir, f"{fname}_{file_prefix}.pin"),
            sep="\t"
        ) for fname in files_list
    ]
    df = pd.concat(dfs, axis=0).reset_index(drop=True)
    # Save merged PSMs features df to be used by Percolator
    df.to_csv(
        os.path.join(rescored_files_dir, f"{file_prefix}.pin"), 
        sep="\t", 
        index=False
    )

    input_file = os.path.join(rescored_files_dir, f"{file_prefix}.pin")
    weights_file = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.weights.csv")
    target_psms = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.psms.txt")
    decoy_psms = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.decoy.psms.txt")
    target_peptides = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.peptides.txt")
    decoy_peptides = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.decoy.peptides.txt")
    # log_file = os.path.join(rescored_files_dir, f"{file_prefix}.log")

    cmd = f"percolator --weights {weights_file} \
            --num-threads {num_threads} \
            --subset-max-train 500000 \
            --post-processing-tdc \
            --testFDR {test_fdr} \
            --trainFDR {train_fdr} \
            --results-psms {target_psms} \
            --decoy-results-psms {decoy_psms} \
            --results-peptides {target_peptides} \
            --decoy-results-peptides {decoy_peptides} \
            {input_file}"
    subprocess.run(cmd, shell=True, check=True)
    print(
        "PSMs rescoring results (.percolator.psms.txt):\n", 
        os.listdir(rescored_files_dir)
    )
    
    
# TODO: remove? (only used for Oktoberfest)
def get_rescoring_config(dset_name, rescoring_config):
    mzml_files_dir = os.path.join(MZML_DATA_DIR, dset_name)
    rescored_files_dir = os.path.join(RESCORED_DATA_DIR, dset_name)
    # oktoberfest only accepts the whole dir, but will try to automatically skip files
    # if rescoring results already exist for them
    spectra = mzml_files_dir  # the location of the mzML file containing the measured spectra, i.e. "<your download_dir>/<filename>.mzml"
    search_results = mzml_files_dir  # the location of the search engine output
    
    rescoring_config = {
        "type": "Rescoring",
        "inputs": {
            "search_results": search_results,
            "search_results_type": "MSFragger",
            "spectra": spectra,
            "spectra_type": rescoring_config.spectra_type
        },
        "output": rescored_files_dir,
        "models": {
            # the model used for fragment intensity prediction, e.g. "some model"
            "intensity": rescoring_config.intensity_model,
            # the model used for retention time prediction, e.g. "some model"
            "irt": rescoring_config.irt_model,
        },
        # the Koina server that provides access to the specified models, e.g. "<url>:<port number>"
        "prediction_server": "koina.wilhelmlab.org:443",
        "ssl": True,
        "numThreads": 1,
        "fdr_estimation_method": "percolator",
        "massTolerance": rescoring_config.massTolerance,
        "unitMassTolerance": rescoring_config.unitMassTolerance,
        # "ce_alignment_options": {
        #     "ce_range": rescoring_config.ce_range
        # }
    }
    
    # TODO: do we really need to save and store it, 
    # or just save -> run -> delete (because we will have a general config instead)
    rescoring_config_path = os.path.join(RESCORE_PARAMS_DIR, f"{dset_name}_rescoring_config.json")
    with open(rescoring_config_path, 'w') as fp:
        json.dump(rescoring_config, fp)
    return rescoring_config_path


def get_filename(psm_id: str):
    """Assumes that there are no `.` in the file name."""
    return psm_id.split(".")[0]

def get_psm_scan_id(psm_id, filename):
    if psm_id.startswith(filename):
        psm_id = psm_id[len(filename):]
    return psm_id.split(".")[1]

def format_peptide_notation(sequence: str):
    """TODO: PTMs may need conversion to ProForma notation."""
    # remove cleavage sites
    if (
        re.match(r"[A-Z-_].*.[A-Z-_]", sequence) is not None
    ):  # check is not mandatory
        sequence = sequence[2:-2]
    return sequence


def create_labeled_mgf(dset_name, labeled_mgf_files_dir, q_val_threshold=0.01):
    file_prefix = "rescore"
    rescored_files_dir = os.path.join(RESCORED_DATA_DIR, dset_name)
    
    # Load DB search + rescoring results
    results_path = os.path.join(rescored_files_dir, f"{file_prefix}.percolator.psms.txt")
    results_df = pd.read_csv(results_path, sep="\t")
    results_df = results_df[results_df["q-value"] < q_val_threshold][["PSMId", "peptide", "q-value"]]

    results_df["filename"] = results_df["PSMId"].apply(get_filename)
    results_df["scan_id"] = results_df[["PSMId", "filename"]].apply(
        lambda row: get_psm_scan_id(row["PSMId"], row["filename"]), 
        axis=1
    )
    results_df["peptide"] = results_df["peptide"].apply(format_peptide_notation)
    
    # TODO: should we take mgf files from repository if available? so far decided that not
    mgf_files_dir = os.path.join(MGF_DATA_DIR, dset_name)
    
    for mgf_file in os.listdir(mgf_files_dir):
        fname = mgf_file.split(".")[0]
        print(fname)
        
        file_labels_df = results_df[results_df["filename"] == fname]
        file_labels_df = file_labels_df.sort_values("scan_id", key=lambda x: x.apply(int))
        file_scan_ids = file_labels_df.scan_id.values.tolist()

        assert len(file_scan_ids) == len(set(file_scan_ids)), "Contains non-unique scan_ids."
        print(len(file_scan_ids))
        file_labels_df = file_labels_df.set_index("scan_id")

        # Load original spectra (.mgf)
        unlabeled_mgf_path = os.path.join(mgf_files_dir, f"{fname}.mgf")
        spectra = mgf.IndexedMGF(unlabeled_mgf_path)
        print("Number of unlabeled spectra:", len(spectra))

        # Annotate spectra if possible, keep annotated only
        labeled_spectra = []
        for spectrum in tqdm(spectra):
            # spectrum["params"]["scans"] = get_d_spectrum_scan_id(spectrum["params"]["title"])
            scan_id = spectrum["params"]["scans"]
            if scan_id in file_scan_ids:
                spectrum["params"]["seq"] = file_labels_df.loc[scan_id, "peptide"]
                labeled_spectra.append(spectrum)
        print("Number of labeled spectra:", len(labeled_spectra))
        del spectra

        # Write annotated spectra (to DATASET_STORAGE_DIR)
        # For large files: split into portions of MAX_SPECTRA_PER_FILE
        idxs = list(range(0, len(labeled_spectra), MAX_SPECTRA_PER_FILE))
        for i, idx in tqdm(enumerate(idxs), total=len(idxs)):
            labeled_mgf_path = os.path.join(labeled_mgf_files_dir, f"{fname}_{i}.mgf")
            if os.path.isfile(labeled_mgf_path): # TODO make optional (add forced re-writing of all files)
                continue
            
            labeled_spectra_i = labeled_spectra[idx: idx + MAX_SPECTRA_PER_FILE]
            mgf.write(
                labeled_spectra_i,
                labeled_mgf_path,
                key_order=MGF_KEY_ORDER,
            #     file_mode="w",
            )
            print(f"{len(labeled_spectra_i)} spectra written to {labeled_mgf_path}.")
