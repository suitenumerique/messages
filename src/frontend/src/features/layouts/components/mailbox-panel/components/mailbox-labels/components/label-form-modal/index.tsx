import { Label, TreeLabel } from "@/features/api/gen";
import { useLabelsCreate, useLabelsList, useLabelsUpdate } from "@/features/api/gen/labels/labels";
import { RhfInput, RhfSelect } from "@/features/forms/components/react-hook-form";
import { useMailboxContext } from "@/features/providers/mailbox";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button, Modal, ModalSize } from "@openfun/cunningham-react";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import z from "zod";
import { RhfColorPaletteField } from "./components/color-palette-field";

export type SubLabelCreation = Partial<Pick<TreeLabel, 'name' | 'color' | 'display_name'>>;

type LabelModalProps = {
    isOpen: boolean;
    onClose: () => void;
    onSuccess?: (label: TreeLabel) => void;
    label?: TreeLabel | SubLabelCreation
}

const formSchema = z.object({
    name: z.string().min(1, { error: 'labels.form.errors.name_required' }),
    color: z.string().regex(/^#([0-9a-fA-F]{6})$/),
    parent_label: z.string().optional(),
});

type FormFields = z.infer<typeof formSchema>;

/**
 * Modal component which contains a form to create/update a label
 */
export const LabelModal = ({ isOpen, onClose, label, onSuccess }: LabelModalProps) => {
    const { t } = useTranslation();
    const defaultValues = useMemo(() => ({
      name: (label as TreeLabel)?.display_name ?? '',
      color: (label as TreeLabel)?.color ?? '#E3E3FD',
      parent_label: label?.name?.split('/').slice(0, -1).join('/') ?? undefined,
    }), [label]);
    const isUpdate = (label as TreeLabel)?.id;
    const form = useForm({
        resolver: zodResolver(formSchema),
        defaultValues,
    });

    const createMutation = useLabelsCreate();
    const updateMutation = useLabelsUpdate();
    const { selectedMailbox } = useMailboxContext();
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const queryClient = useQueryClient();
    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox!.id })
    const flattenLabels = useMemo(() => {
      if (!labelsQuery.data) return [];

      type TreeLabelOption = {
        label: string;
        value: string;
        level: number;
      }

      const flatten = (labels: readonly TreeLabel[], level = 0): TreeLabelOption[] => {
        return labels.map((label) => (
          [
            { label: label.display_name, value: label.name, level },
            ...flatten(label.children, level + 1)
          ]
        )).flat();
      }

      return flatten((labelsQuery.data.data)).filter(
        // Do not display current label and its children as options to nest the current label
        (option) => !label?.name || !option.value.startsWith(label.name)
      );
    }, [label, labelsQuery.data]);

    const handleClose = () => {
      onClose();
    }

    const handleSubmit = (data: FormFields) => {
      const mutation = isUpdate ? updateMutation : createMutation;

      mutation.mutate({
        id: (label as TreeLabel)?.id || '',
        data: {
          name: data.parent_label ? `${data.parent_label}/${data.name}` : data.name,
          color: data.color,
          mailbox: selectedMailbox!.id,
        }
      }, {
        onSuccess: async (data) => {
          await queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
          // If the active label has been updated, update the search params
          if (isUpdate && searchParams.get('label_slug') === (label as TreeLabel)?.slug) {
            const newSearchParams = new URLSearchParams(searchParams.toString());
            newSearchParams.set('label_slug', (data.data as Label).slug);
            router.push(`${pathname}?${newSearchParams.toString()}`);
          }
          onSuccess?.(data.data as TreeLabel);
          handleClose();
        }
      });
    }

    useEffect(() => {
      if (isOpen) {
        form.reset(defaultValues);
      }
    }, [isOpen]);

    return (
      <Modal
        size={ModalSize.SMALL}
        isOpen={isOpen}
        onClose={handleClose}
        title={isUpdate ? t('labels.update') : t('labels.create')}
        closeOnClickOutside
      >
        <FormProvider {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)} className="label-form">
            <div className="form-field-row">
              <RhfInput
                name="name"
                label={t('labels.form.fields.name')}
                text={form.formState.errors.name?.message && t(form.formState.errors.name.message)}
                fullWidth
              />
            </div>
            <div className="form-field-row">
              <RhfSelect
                name="parent_label"
                label={t('labels.form.fields.parent_label')}
                options={flattenLabels.map((option) => ({
                  label: option.label,
                  value: option.value,
                  render: () => (
                    <div className="label-form__select-option" style={{ paddingLeft: `${option.level * 1.5}rem` }}>
                      <span className="label-form__select-option__value">{option.label}</span>
                    </div>
                  )
                }))}
                searchable
                fullWidth
              />
            </div>
            <div className="form-field-row">
              <RhfColorPaletteField name="color" />
            </div>
            <footer className="form-field-row">
              <Button type="button" color="secondary" size="medium" onClick={onClose}>
                {t('actions.cancel')}
              </Button>
              <Button type="submit" color="primary" size="medium">
                {isUpdate ? t('labels.form.submit_update') : t('labels.form.submit_create')}
              </Button>
            </footer>
          </form>
        </FormProvider>
      </Modal>
    )
  }
