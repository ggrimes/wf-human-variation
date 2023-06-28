import groovy.json.JsonBuilder

include {
    filterBam;
    sniffles2;
    filterCalls;
    sortVCF;
    indexVCF;
    getVersions;
    getParams;
    report;
} from "../modules/local/wf-human-sv.nf"
include {
    filterBenchmarkVcf;
    intersectBedWithTruthset;
    truvari;
} from "../modules/local/wf-human-sv-eval.nf"
include { 
    annotate_vcf as annotate_sv_vcf
} from '../modules/local/common.nf'

workflow bam {
    take:
        bam_channel
        reference
        target
        mosdepth_stats
        optional_file
        genome_build
        extensions
    main:
        // truncate bam channel to remove meta to keep compat with sv pipe
        bam = bam_channel.map{ it -> tuple(it[0], it[1]) }
        called = variantCall(bam, reference, target, mosdepth_stats, optional_file, genome_build, extensions)

        // benchmark
        if (params.sv_benchmark) {
            benchmark_result = runBenchmark(called.vcf, reference, target)
        }
        else {
            benchmark_result = Channel.fromPath(optional_file)
        }

        if (params.skip_annotation) {
            final_vcf = called.vcf.combine(called.vcf_index)
            // no ClinVar VCF, pass empty file to makeReport
            empty_file = projectDir.resolve("./data/empty.txt").toString()
            report = runReport(
                called.vcf.collect(),
                empty_file,
                benchmark_result
            )
        }
        else {
            vcf_for_annotation = called.vcf.combine(called.vcf_index)
            // do annotation and get a list of ClinVar variants for the report
            annotations = annotate_sv_vcf(vcf_for_annotation, genome_build, "sv")
            final_vcf = annotations.final_vcf
            report = runReport(
                final_vcf.map{it[0]},
                annotations.final_vcf_clinvar,
                benchmark_result
            )
        }

    emit:
        report = report.html.concat(
            final_vcf,
            benchmark_result
        )
        sniffles_vcf = called.vcf
}


workflow runBenchmark {
    take:
        vcf
        reference
        target
    main:
        // for benchmarking we bundle a dataset in the SV container in $WFSV_EVAL_DATA_PATH
        // rather than coupling that dataset to the workflow by referring to it here
        //   in a value channel (or similar), we'll instead interpret use of dummy files
        //   as a flag to load from the bundled dataset inside the process scope
        // note we're not using the usual `optional_file` as this will cause an input collision error
        //   instead we just reference some OPTIONAL_FILE.ext that we know don't exist
        //   we can get away with this as the files will never be opened (so don't need to exist)

        // reconcile workflow target BED and benchmark truthset BED
        //   recall if user does not input a BED, one covering all genomic
        //   intervals in the ref is generated by getAllChromosomesBed
        if (params.sv_benchmark_bed) {
            truthset_bed = Channel.fromPath(params.sv_benchmark_bed, checkIfExists: true)
        }
        else {
            truthset_bed = file("OPTIONAL_FILE.bed") // this will trigger process to use bundled benchmark bed
        }
        intersected = intersectBedWithTruthset(target, truthset_bed)

        // load user-provided benchmark data
        if (params.sv_benchmark_vcf) {
            // truvari assumes index is [vcf].tbi
            truthset_vcf = Channel.fromPath(params.sv_benchmark_vcf, checkIfExists: true)
            truthset_tbi = Channel.fromPath(params.sv_benchmark_vcf + '.tbi', checkIfExists: true)
        }
        else {
            // we'll create some non-existent optional files to stage
            // again this will trigger the process to use the bundled benchmark data
            // we use channels here so we can concat them later
            truthset_vcf = Channel.fromPath("OPTIONAL_FILE.vcf.gz", checkIfExists: false)
            truthset_tbi = Channel.fromPath("OPTIONAL_FILE.vcf.gz.tbi", checkIfExists: false)
        }

        // run benchmark
        filtered = filterBenchmarkVcf(vcf)
        truvari(
            reference,
            filtered,
            truthset_vcf.concat(truthset_tbi).toList(),
            intersected.intersected_bed)
    emit:
        json = truvari.out.truvari_json
}


workflow variantCall {
    take:
        bam
        reference
        target_bed
        mosdepth_stats
        optional_file
        genome_build
        extensions
    main:

        // tandom_repeat bed
        if(params.tr_bed == null) {
            tr_bed = optional_file
        } else {
            tr_bed = Channel.fromPath(params.tr_bed, checkIfExists: true)
        }

        filterBam(bam, reference, extensions)
        sniffles2(filterBam.out.xam, tr_bed, reference)
        filterCalls(sniffles2.out.vcf, mosdepth_stats, target_bed)
        sortVCF(filterCalls.out.vcf)
        indexVCF(sortVCF.out.vcf)

    emit:
        vcf = indexVCF.out.vcf_gz
        vcf_index = indexVCF.out.vcf_tbi
}


workflow runReport {
    take:
        vcf
        clinvar_vcf
        eval_json
    main:
        software_versions = getVersions()
        workflow_params = getParams()
        report(
            vcf.collect(),
            clinvar_vcf,
            eval_json,
            software_versions,
            workflow_params,
        )
    emit:
        html = report.out.html
}
